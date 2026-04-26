"""Phase 5 — Report Composition.

Public API:
    compose(themes, plan, corpus_stats, ingest_results) -> tuple[DocReport, EmailReport]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_5.anchor import anchor_for
from pulse.phase_5.doc_blocks import build_doc_report
from pulse.phase_5.email_render import render_email_report
from pulse.phase_5.types import DocReport, EmailReport

if TYPE_CHECKING:
    from pulse.phase_1.ingestion.base import IngestResult
    from pulse.phase_2.core.types import CorpusStats
    from pulse.phase_4.core.types import Theme
    from pulse.phase_0.core.types import RunPlan

log = structlog.get_logger()

__all__ = ["compose", "DocReport", "EmailReport"]


def compose(
    themes: list[Theme],
    plan: RunPlan,
    corpus_stats: CorpusStats,
    ingest_results: dict[str, IngestResult],
    *,
    fallback_used: bool = False,
) -> tuple[DocReport, EmailReport]:
    """Render themes into DocReport and EmailReport.

    Determines missing sources from ingest_results (sources with 0 reviews).
    Raises PhaseFailure(5) if rendering produces empty output.
    """
    configured_sources = set(plan.sources)
    sources_with_data = {
        src for src, res in ingest_results.items() if len(res.reviews) > 0
    }
    missing_sources = sorted(configured_sources - sources_with_data)

    anchor = anchor_for(plan.product.slug, plan.iso_week)

    try:
        doc_report = build_doc_report(
            themes,
            plan,
            corpus_stats,
            missing_sources=missing_sources,
            fallback_used=fallback_used,
        )
        email_report = render_email_report(themes, plan, anchor)
    except Exception as exc:
        log.error("phase_5_render_failed", error=str(exc))
        raise PhaseFailure(5, f"render_failed: {exc}") from exc

    if not doc_report.blocks:
        raise PhaseFailure(5, "doc_report_empty")

    return doc_report, email_report
