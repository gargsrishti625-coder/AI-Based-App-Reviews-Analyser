"""Per-cluster LLM theming orchestrator."""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import structlog

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_3.core.types import Cluster
from pulse.phase_4.core.types import Quote, Theme
from pulse.llm.budget import Budget, BudgetExceeded
from pulse.llm.prompts import PROMPT_VERSION, SYSTEM_PROMPT, _JSON_RETRY_SUFFIX, build_user_prompt
from pulse.llm.validate import validate_quote

if TYPE_CHECKING:
    from pulse.phase_2.core.types import CleanReview

log = structlog.get_logger()

_MAX_ACTION_WORDS = 12


async def theme_cluster(
    cluster: Cluster,
    reviews_by_id: dict[str, CleanReview],
    budget: Budget,
    model: str,
    semaphore: asyncio.Semaphore,
) -> Theme | None:
    """Call the LLM for one cluster; return a validated Theme or None.

    Returns None when:
    - No centroid reviews exist.
    - LLM returns null (no discernible theme).
    - JSON parsing fails after one retry.
    - All proposed quotes fail the hard validation gate.
    Raises BudgetExceeded (propagated to caller) when the cap would be exceeded.
    """
    import groq  # lazy — non-theming paths pay no import cost

    centroid_reviews = [
        reviews_by_id[rid]
        for rid in cluster.centroid_review_ids
        if rid in reviews_by_id
    ]
    if not centroid_reviews:
        return None

    user_prompt = build_user_prompt(cluster.cluster_id, centroid_reviews)
    # Conservative token estimate: chars/4 + 500 output headroom
    estimated = (len(SYSTEM_PROMPT) + len(user_prompt)) // 4 + 500
    budget.check(estimated)

    client = groq.AsyncGroq()
    parsed: dict | None = None

    async with semaphore:
        current_user = user_prompt
        for attempt in range(2):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    max_tokens=1000,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": current_user},
                    ],
                )
                budget.add(resp.usage.prompt_tokens + resp.usage.completion_tokens)
                raw = (resp.choices[0].message.content or "").strip()
                if raw.lower() in ("null", ""):
                    return None
                parsed = json.loads(raw)
                if parsed is None:
                    return None
                break
            except json.JSONDecodeError:
                if attempt == 0:
                    log.warning("phase_4_json_retry", cluster_id=cluster.cluster_id)
                    current_user = user_prompt + _JSON_RETRY_SUFFIX
                    continue
                log.warning("phase_4_json_failed_twice", cluster_id=cluster.cluster_id)
                return None
            except BudgetExceeded:
                raise
            except Exception as exc:
                log.error("phase_4_llm_error", cluster_id=cluster.cluster_id, error=str(exc))
                return None

    if parsed is None:
        return None

    # --- Hard quote validation gate ---
    raw_quotes = parsed.get("quotes") or []
    validated: list[Quote] = []
    for item in raw_quotes:
        if not isinstance(item, dict):
            continue
        q = Quote(
            text=str(item.get("text", "")),
            review_id=str(item.get("review_id", "")),
        )
        if validate_quote(q, cluster, reviews_by_id):
            validated.append(q)
        else:
            log.info(
                "phase_4_quote_dropped",
                cluster_id=cluster.cluster_id,
                review_id=q.review_id,
            )

    if not validated:
        log.info("phase_4_theme_dropped_no_valid_quotes", cluster_id=cluster.cluster_id)
        return None

    # --- Action ideas: drop any over the word limit ---
    action_ideas: list[str] = []
    for idea in (parsed.get("action_ideas") or []):
        if not isinstance(idea, str):
            continue
        if len(idea.split()) <= _MAX_ACTION_WORDS:
            action_ideas.append(idea)
        else:
            log.info(
                "phase_4_action_idea_too_long",
                cluster_id=cluster.cluster_id,
                word_count=len(idea.split()),
                idea_preview=idea[:60],
            )

    return Theme(
        title=str(parsed.get("title", ""))[:60],
        summary=str(parsed.get("summary", "")),
        quotes=validated,
        action_ideas=action_ideas,
        supporting_review_ids=cluster.member_review_ids,
        cluster_id=cluster.cluster_id,
    )


async def theme_clusters(
    clusters: list[Cluster],
    reviews_by_id: dict[str, CleanReview],
    budget: Budget,
    model: str,
    max_concurrency: int = 3,
) -> list[Theme]:
    """Theme all clusters in parallel (bounded by Semaphore).

    Budget exhaustion stops new calls but keeps results already gathered.
    Near-duplicate theme titles (Jaccard > 0.7) are de-duplicated, keeping the
    first (highest-ranked) occurrence.
    Raises PhaseFailure(4) if no themes survive.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    budget_exhausted = False

    async def _safe(cluster: Cluster) -> Theme | None:
        nonlocal budget_exhausted
        if budget_exhausted:
            return None
        try:
            return await theme_cluster(cluster, reviews_by_id, budget, model, semaphore)
        except BudgetExceeded:
            budget_exhausted = True
            log.warning("phase_4_budget_exhausted", used=budget.used, cap=budget.cap)
            return None

    raw_results = await asyncio.gather(*[_safe(c) for c in clusters])

    # Jaccard dedup on title token sets
    def _jaccard(a: str, b: str) -> float:
        ta, tb = set(a.lower().split()), set(b.lower().split())
        if not ta and not tb:
            return 1.0
        return len(ta & tb) / len(ta | tb)

    themes: list[Theme] = []
    for theme in raw_results:
        if theme is None:
            continue
        if any(_jaccard(theme.title, t.title) > 0.7 for t in themes):
            log.info("phase_4_theme_deduped", title=theme.title)
            continue
        themes.append(theme)

    log.info(
        "phase_4_done",
        themes_kept=len(themes),
        themes_proposed=sum(1 for r in raw_results if r is not None),
        budget_used=budget.used,
        budget_exhausted=budget_exhausted,
        prompt_version=PROMPT_VERSION,
    )

    if not themes:
        raise PhaseFailure(4, "no_validated_themes")

    return themes
