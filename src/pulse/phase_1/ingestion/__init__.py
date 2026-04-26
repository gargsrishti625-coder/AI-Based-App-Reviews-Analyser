"""Phase 1 orchestrator — ingest reviews from all configured sources."""
from __future__ import annotations

import asyncio

import structlog

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.types import PulseConfig, RunPlan

from .app_store import AppStoreIngester
from .base import IngestResult, RawReview
from .play_store import PlayStoreIngester

log = structlog.get_logger()

_INGESTERS: dict[str, object] = {
    "app_store": AppStoreIngester(),
    "play_store": PlayStoreIngester(),
}


async def ingest(plan: RunPlan, config: PulseConfig) -> dict[str, IngestResult]:
    """Run all configured sources concurrently.

    Soft-failure semantics: a persistent error from one source returns an
    IngestResult with status="failed" rather than aborting the whole run.
    PhaseFailure(1, ...) is raised only when the combined review count is
    below N_min AND no source succeeded.
    """
    window = (plan.window_start, plan.window_end)
    cap = config.max_reviews_per_source

    async def _fetch_safe(src: str) -> tuple[str, IngestResult]:
        ingester = _INGESTERS[src]
        try:
            result = await ingester.fetch(plan.product, window, cap)  # type: ignore[union-attr]
        except Exception as exc:
            log.error("ingestion_source_failed", source=src, error=str(exc))
            result = IngestResult(
                source=src,
                status="failed",
                error=str(exc),
            )
        log.info(
            "ingestion_source_done",
            source=src,
            status=result.status,
            reviews=len(result.reviews),
            pages=result.pages_fetched,
            capped=result.capped,
        )
        return src, result

    pairs = await asyncio.gather(*[_fetch_safe(src) for src in plan.sources])
    results: dict[str, IngestResult] = dict(pairs)

    total = sum(len(r.reviews) for r in results.values())
    all_non_ok = all(r.status in ("failed", "empty") for r in results.values())

    if total == 0 and all_non_ok:
        raise PhaseFailure(1, "all_sources_empty_or_failed: no reviews ingested")

    if total < config.n_min_reviews:
        log.warning(
            "review_count_below_minimum",
            total=total,
            n_min=config.n_min_reviews,
        )

    return results
