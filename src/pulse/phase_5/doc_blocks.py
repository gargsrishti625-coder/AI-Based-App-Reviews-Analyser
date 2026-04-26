"""Build the structured DocReport block list for Docs MCP batchUpdate."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from pulse.phase_5.anchor import anchor_for
from pulse.phase_5.types import DocBlock, DocReport

if TYPE_CHECKING:
    from pulse.phase_2.core.types import CorpusStats
    from pulse.phase_4.core.types import Theme
    from pulse.phase_0.core.types import RunPlan

log = structlog.get_logger()

_MAX_HEADING_LEN = 80   # truncate very long theme titles in H3


def build_doc_report(
    themes: list[Theme],
    plan: RunPlan,
    corpus_stats: CorpusStats,
    *,
    missing_sources: list[str] | None = None,
    fallback_used: bool = False,
) -> DocReport:
    """Build the full DocReport block list from validated themes.

    Pure function — no I/O, no timestamps, fully deterministic given the same
    inputs.  Phase 6 passes this directly to docs.batchUpdate.
    """
    t0 = time.monotonic()
    missing_sources = missing_sources or []
    section_anchor = anchor_for(plan.product.slug, plan.iso_week)

    blocks: list[DocBlock] = []

    # ── H2 week heading (anchored) ─────────────────────────────────────────
    h2_text = (
        f"Week of {plan.window_end:%Y-%m-%d} "
        f"(ISO {plan.iso_week}) — {corpus_stats.total_out} reviews"
    )
    blocks.append(DocBlock(type="heading_2", text=h2_text, anchor=section_anchor))

    # ── One section per theme ──────────────────────────────────────────────
    total_quotes = 0
    total_actions = 0
    for theme in themes:
        title = theme.title[:_MAX_HEADING_LEN]
        blocks.append(DocBlock(type="heading_3", text=title))
        blocks.append(DocBlock(type="paragraph", text=theme.summary))

        for quote in theme.quotes:
            blocks.append(
                DocBlock(
                    type="blockquote",
                    text=quote.text,
                    attribution=quote.review_id,
                )
            )
            total_quotes += 1

        if theme.action_ideas:
            blocks.append(DocBlock(type="heading_3", text="Action ideas"))
            for idea in theme.action_ideas:
                blocks.append(DocBlock(type="bullet", text=idea))
                total_actions += 1

    # ── Footer ────────────────────────────────────────────────────────────
    window_str = (
        f"{plan.window_start:%Y-%m-%d} → {plan.window_end:%Y-%m-%d}"
    )
    source_counts = ", ".join(
        f"{s}={corpus_stats.total_in}" for s in plan.sources
    )
    blocks.append(
        DocBlock(
            type="paragraph",
            text=(
                f"LLM: {plan.llm_model} · "
                f"Window: {window_str} · "
                f"Sources: {source_counts}"
            ),
        )
    )

    if fallback_used:
        blocks.append(
            DocBlock(
                type="paragraph",
                text="Low-volume week — themes derived by rating bucket, not clustering.",
            )
        )

    for src in missing_sources:
        blocks.append(
            DocBlock(
                type="paragraph",
                text=f"{src.replace('_', ' ').title()} unavailable this week.",
            )
        )

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    log.info(
        "phase_5_doc_built",
        anchor=section_anchor,
        doc_block_count=len(blocks),
        themes_rendered=len(themes),
        total_quotes_rendered=total_quotes,
        total_actions_rendered=total_actions,
        doc_render_duration_ms=elapsed_ms,
    )

    return DocReport(
        anchor=section_anchor,
        blocks=blocks,
        metadata={
            "run_id": str(plan.run_id),
            "product": plan.product.slug,
            "iso_week": plan.iso_week,
            "llm_model": plan.llm_model,
            "themes_rendered": len(themes),
            "total_quotes_rendered": total_quotes,
        },
    )
