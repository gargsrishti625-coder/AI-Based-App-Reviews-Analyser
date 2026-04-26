"""Reusable pipeline orchestrator.

Lifts the body of `pulse run` into a function so that `pulse run`,
`pulse backfill`, and `scheduler/weekly.py` all share the same path.

The function returns a :class:`PipelineOutcome` rather than raising
:class:`typer.Exit`, so multi-week / multi-product callers can aggregate
exit codes without intercepting Typer exceptions.
"""
from __future__ import annotations

import asyncio
import json as _json
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as _tz
from pathlib import Path
from typing import Literal
from uuid import UUID

import typer

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.runplan import RunPlan
from pulse.phase_0.core.types import PulseConfig
from pulse.phase_7 import AuditRecord, AuditStore, Decision, check_before_run

PipelineStatus = Literal["ok", "skipped", "partial", "failed"]


@dataclass(frozen=True)
class PipelineOutcome:
    """Structured result of one pipeline execution.

    exit_code follows Phase 8's contract:
      ok / skipped → 0
      partial      → 2
      failed       → 1
    """

    status: PipelineStatus
    exit_code: int
    run_id: UUID
    failed_phase: int | None = None
    error: str | None = None

    @classmethod
    def ok(cls, run_id: UUID) -> "PipelineOutcome":
        return cls("ok", 0, run_id)

    @classmethod
    def skipped(cls, run_id: UUID, reason: str) -> "PipelineOutcome":
        return cls("skipped", 0, run_id, error=reason)

    @classmethod
    def partial(cls, run_id: UUID, reason: str) -> "PipelineOutcome":
        return cls("partial", 2, run_id, error=reason)

    @classmethod
    def failed(
        cls, run_id: UUID, phase: int, reason: str
    ) -> "PipelineOutcome":
        return cls("failed", 1, run_id, failed_phase=phase, error=reason)


def _run_dir(run_id: str) -> Path:
    d = Path(".pulse") / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def execute_pipeline(
    plan: RunPlan,
    cfg: PulseConfig,
    audit_store: AuditStore,
) -> PipelineOutcome:
    """Run phases 1–7 for *plan* and persist a terminal audit record.

    Honours the idempotency guard: a second run for the same
    (product, iso_week) returns ``skipped`` unless ``plan.force_resend``
    is set.
    """
    decision = check_before_run(audit_store, plan)
    if decision == Decision.SKIP_ALREADY_SENT:
        typer.echo(
            f"[phase7] Already sent for {plan.product.slug} {plan.iso_week}. "
            "Use --force-resend to override.",
            err=True,
        )
        return PipelineOutcome.skipped(plan.run_id, "already_sent")

    started_at = datetime.now(_tz.utc)
    audit_record = AuditRecord(
        run_id=plan.run_id,
        product=plan.product.slug,
        iso_week=plan.iso_week,
        started_at=started_at,
        status="failed",
        error="in_flight",
        window_start=plan.window_start,
        window_end=plan.window_end,
        llm_model=plan.llm_model,
        forced=plan.force_resend,
        dry_run=plan.dry_run,
        doc_id=plan.product.pulse_doc_id,
    )
    audit_store.insert(audit_record)

    # Phase 1 — ingest
    from pulse.phase_1.ingestion import ingest

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        audit_store.update_terminal(
            plan.run_id,
            status="failed",
            failed_phase=exc.phase,
            error=exc.reason,
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.failed(plan.run_id, exc.phase, exc.reason)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]

    run_dir = _run_dir(str(plan.run_id))
    raw_path = run_dir / "raw.jsonl"
    with open(raw_path, "w", encoding="utf-8") as fh:
        for r in raw_reviews:
            fh.write(r.model_dump_json() + "\n")
    typer.echo(f"[phase1] {len(raw_reviews)} raw reviews → {raw_path}", err=True)

    # Phase 2 — clean
    from pulse.phase_2.preprocess import clean

    clean_reviews, corpus_stats = clean(raw_reviews)
    clean_path = run_dir / "clean.jsonl"
    with open(clean_path, "w", encoding="utf-8") as fh:
        for r in clean_reviews:
            fh.write(r.model_dump_json() + "\n")
    typer.echo(
        f"[phase2] total_in={corpus_stats.total_in} "
        f"total_out={corpus_stats.total_out} "
        f"dropped_short={corpus_stats.dropped_short} "
        f"dropped_lang={corpus_stats.dropped_lang} "
        f"dedup={corpus_stats.dedup_count} "
        f"→ {clean_path}",
        err=True,
    )

    if len(clean_reviews) < cfg.n_min_reviews:
        reason = f"too_few_reviews:{len(clean_reviews)}<{cfg.n_min_reviews}"
        typer.echo(
            f"Phase 2: only {len(clean_reviews)} reviews after cleaning "
            f"(minimum {cfg.n_min_reviews}). Aborting.",
            err=True,
        )
        audit_store.update_terminal(
            plan.run_id,
            status="skipped",
            corpus_stats=corpus_stats,
            error=reason,
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.skipped(plan.run_id, reason)

    # Phase 3 — cluster
    from pulse.phase_3.cluster import cluster_reviews
    from pulse.phase_3.cluster.embed import SentenceTransformerEmbedder

    cache_path = run_dir / "embed_cache.db"
    embedder = SentenceTransformerEmbedder()
    try:
        clustering = asyncio.run(
            cluster_reviews(clean_reviews, embedder, cache_path)
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        audit_store.update_terminal(
            plan.run_id,
            status="failed",
            failed_phase=exc.phase,
            error=exc.reason,
            corpus_stats=corpus_stats,
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.failed(plan.run_id, exc.phase, exc.reason)

    typer.echo(
        f"[phase3] {len(clustering.clusters)} clusters "
        f"(fallback={clustering.fallback_used}, silhouette={clustering.silhouette})",
        err=True,
    )

    # Phase 4 — LLM theming
    from pulse.llm import theme_clusters
    from pulse.llm.budget import Budget

    reviews_by_id = {r.review_id: r for r in clean_reviews}
    budget = Budget(cfg.total_token_cap)
    try:
        themes = asyncio.run(
            theme_clusters(
                clustering.clusters, reviews_by_id, budget, plan.llm_model
            )
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        audit_store.update_terminal(
            plan.run_id,
            status="failed",
            failed_phase=exc.phase,
            error=exc.reason,
            corpus_stats=corpus_stats,
            cluster_count=len(clustering.clusters),
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.failed(plan.run_id, exc.phase, exc.reason)

    themes_path = run_dir / "themes.json"
    with open(themes_path, "w", encoding="utf-8") as fh:
        _json.dump([t.model_dump() for t in themes], fh, indent=2)
    typer.echo(
        f"[phase4] {len(themes)} themes (budget_used={budget.used}) → {themes_path}",
        err=True,
    )

    # Phase 5 — report composition
    from pulse.phase_5 import compose

    try:
        doc_report, email_report = compose(
            themes,
            plan,
            corpus_stats,
            ingest_results,
            fallback_used=clustering.fallback_used,
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        audit_store.update_terminal(
            plan.run_id,
            status="failed",
            failed_phase=exc.phase,
            error=exc.reason,
            corpus_stats=corpus_stats,
            cluster_count=len(clustering.clusters),
            theme_count=len(themes),
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.failed(plan.run_id, exc.phase, exc.reason)

    doc_report_path = run_dir / "doc_report.json"
    with open(doc_report_path, "w", encoding="utf-8") as fh:
        _json.dump(doc_report.model_dump(), fh, indent=2)

    email_html_path = run_dir / "email.html"
    email_txt_path = run_dir / "email.txt"
    email_html_path.write_text(email_report.html_body, encoding="utf-8")
    email_txt_path.write_text(email_report.text_body, encoding="utf-8")

    typer.echo(
        f"[phase5] {len(doc_report.blocks)} doc blocks, anchor={doc_report.anchor} "
        f"→ {doc_report_path}",
        err=True,
    )

    # Phase 6 — MCP delivery
    from pulse.phase_6 import deliver

    try:
        receipt = asyncio.run(deliver(plan, doc_report, email_report))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        audit_store.update_terminal(
            plan.run_id,
            status="failed",
            failed_phase=exc.phase,
            error=exc.reason,
            corpus_stats=corpus_stats,
            cluster_count=len(clustering.clusters),
            theme_count=len(themes),
            total_tokens=budget.used,
            ended_at=datetime.now(_tz.utc),
        )
        return PipelineOutcome.failed(plan.run_id, exc.phase, exc.reason)

    receipt_path = run_dir / "receipt.json"
    receipt_path.write_text(
        _json.dumps(receipt.model_dump(), indent=2, default=str),
        encoding="utf-8",
    )
    typer.echo(
        f"[phase6] doc_status={receipt.doc_status} "
        f"email_status={receipt.email_status} → {receipt_path}",
        err=True,
    )

    # Phase 7 — finalise audit record
    final_status: PipelineStatus = (
        "ok"
        if receipt.email_status
        in {"sent", "drafted", "skipped_already_sent", "dry_run"}
        else "partial"
    )
    audit_store.update_terminal(
        plan.run_id,
        status=final_status,
        corpus_stats=corpus_stats,
        cluster_count=len(clustering.clusters),
        theme_count=len(themes),
        total_tokens=budget.used,
        doc_section_anchor=receipt.doc_section_anchor,
        doc_revision_id=receipt.doc_revision_id,
        gmail_message_id=receipt.gmail_message_id,
        gmail_draft_id=receipt.gmail_draft_id,
        error=None,
        ended_at=datetime.now(_tz.utc),
    )
    typer.echo(f"[phase7] audit written — status={final_status}", err=True)

    if final_status == "ok":
        return PipelineOutcome.ok(plan.run_id)
    return PipelineOutcome.partial(
        plan.run_id, f"email_status={receipt.email_status}"
    )
