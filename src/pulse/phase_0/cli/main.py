from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from pydantic import ValidationError

from pulse.phase_0.config.loader import load_config
from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.runplan import (
    bootstrap,
    last_completed_iso_week_ist,
    parse_iso_week,
)
from pulse.phase_0.obs import logger as obs
from pulse.util.paths import get_pulse_dir

app = typer.Typer(
    name="pulse",
    help="Weekly Product Review Pulse — run, backfill, audit, and probe.",
    no_args_is_help=True,
)


@app.callback()
def _root_callback() -> None:
    """Load .env on every CLI invocation (but not on bare module import)."""
    from dotenv import load_dotenv

    load_dotenv()
audit_app = typer.Typer(help="Inspect audit records.")
app.add_typer(audit_app, name="audit")

mcp_app = typer.Typer(help="MCP server utilities.")
app.add_typer(mcp_app, name="mcp")

debug_app = typer.Typer(help="Debug commands that write intermediate artifacts to disk.")
app.add_typer(debug_app, name="debug")

_CONFIG_ENV = "PULSE_CONFIG"
_DEFAULT_CONFIG = Path("config/pulse.yaml")


def _resolve_config_path(config: Path | None) -> Path:
    if config:
        return config
    env_path = os.environ.get(_CONFIG_ENV)
    return Path(env_path) if env_path else _DEFAULT_CONFIG


def _run_dir(run_id: str) -> Path:
    """Return (and create) the per-run artifact directory."""
    d = get_pulse_dir() / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# pulse run
# ---------------------------------------------------------------------------


@app.command()
def run(
    product: Annotated[str, typer.Option("--product", "-p", help="Product slug from registry.")],
    week: Annotated[
        Optional[str],
        typer.Option("--week", "-w", help="ISO week to run, e.g. 2026-W17. Defaults to last completed week in IST."),
    ] = None,
    draft_only: Annotated[
        Optional[bool],
        typer.Option("--draft-only/--no-draft-only", help="Send Gmail as draft. Defaults to True for non-prod."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Build plan and log it; skip all MCP writes."),
    ] = False,
    force_resend: Annotated[
        bool,
        typer.Option("--force-resend", help="Bypass idempotency guard and resend the email."),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option("--config", "-c", help="Path to YAML config file."),
    ] = None,
    json_logs: Annotated[
        bool,
        typer.Option("--json-logs/--no-json-logs", help="Emit logs as JSON (default True)."),
    ] = True,
) -> None:
    """Run the pulse pipeline for a single product and week."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except FileNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(64)
    except ValidationError as exc:
        typer.echo(f"Config validation error:\n{exc}", err=True)
        raise typer.Exit(64)
    except ValueError as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    # ── Phase 8 validation guards ────────────────────────────────────────
    try:
        _validate_run_args(week=week, force_resend=force_resend)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(64)

    if dry_run and draft_only is False:
        typer.echo(
            "[run] --dry-run dominates --no-draft-only: no MCP writes will happen.",
            err=True,
        )

    try:
        plan = bootstrap(
            config=cfg,
            product_slug=product,
            iso_week=week,
            dry_run=dry_run,
            draft_only=draft_only,
            force_resend=force_resend,
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    if dry_run or cfg.pulse_env == "dev":
        plan_data = {
            "run_id": str(plan.run_id),
            "product": plan.product.slug,
            "iso_week": plan.iso_week,
            "window_start": plan.window_start.isoformat(),
            "window_end": plan.window_end.isoformat(),
            "sources": plan.sources,
            "llm_model": plan.llm_model,
            "dry_run": plan.dry_run,
            "draft_only": plan.draft_only,
        }
        typer.echo(json.dumps(plan_data, indent=2))

    from pulse.phase_7 import AuditStore
    from pulse.scheduler.pipeline import execute_pipeline

    audit_db = get_pulse_dir() / "audit.db"
    audit_store = AuditStore(audit_db)
    audit_store.migrate()

    outcome = execute_pipeline(plan, cfg, audit_store)
    raise typer.Exit(outcome.exit_code)


def _validate_run_args(*, week: str | None, force_resend: bool) -> None:
    """Reject ambiguous / nonsensical CLI arg combinations before bootstrap.

    Raises ValueError with a human-friendly message; the caller maps this
    to exit code 64 (EX_USAGE / config error).
    """
    if force_resend and week is None:
        raise ValueError(
            "--force-resend requires --week to be set explicitly. "
            "Without an explicit week the target of the resend is ambiguous "
            "(the default 'last completed week' shifts day-to-day)."
        )

    if week is None:
        return

    try:
        user_year, user_week = parse_iso_week(week)
    except ValueError as exc:
        raise ValueError(f"Invalid --week: {exc}") from exc

    last_completed = last_completed_iso_week_ist()
    last_year, last_week = parse_iso_week(last_completed)
    if (user_year, user_week) > (last_year, last_week):
        raise ValueError(
            f"Week {week} is not yet completed (last completed: {last_completed}). "
            f"Pulse only operates on fully closed ISO weeks."
        )


# ---------------------------------------------------------------------------
# pulse backfill
# ---------------------------------------------------------------------------


@app.command()
def backfill(
    product: Annotated[str, typer.Option("--product", "-p")],
    weeks: Annotated[str, typer.Option("--weeks", help="Inclusive range, e.g. 2026-W10..2026-W17.")],
    draft_only: Annotated[Optional[bool], typer.Option("--draft-only/--no-draft-only")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run")] = False,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = True,
) -> None:
    """Backfill a range of ISO weeks for a product."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    parts = weeks.split("..")
    if len(parts) != 2:
        typer.echo("--weeks must be in the form 2026-W10..2026-W17", err=True)
        raise typer.Exit(64)
    start_week, end_week = parts[0].strip(), parts[1].strip()

    try:
        sy, sw = parse_iso_week(start_week)
        ey, ew = parse_iso_week(end_week)
        week_list = _expand_week_range(sy, sw, ey, ew)
    except ValueError as exc:
        typer.echo(f"Invalid week range: {exc}", err=True)
        raise typer.Exit(64)

    # Reject any week in the range that isn't yet completed.
    last_completed = last_completed_iso_week_ist()
    last_year, last_week = parse_iso_week(last_completed)
    incomplete = [
        w for w in week_list
        if (lambda y, n: (y, n) > (last_year, last_week))(*parse_iso_week(w))
    ]
    if incomplete:
        typer.echo(
            f"Range contains weeks not yet completed: {incomplete} "
            f"(last completed: {last_completed}).",
            err=True,
        )
        raise typer.Exit(64)

    from pulse.phase_7 import AuditStore
    from pulse.scheduler.pipeline import execute_pipeline

    audit_db = get_pulse_dir() / "audit.db"
    audit_store = AuditStore(audit_db)
    audit_store.migrate()

    worst_exit = 0
    for iso_week in week_list:
        typer.echo(f"[backfill] === {product} / {iso_week} ===", err=True)
        try:
            plan = bootstrap(
                config=cfg,
                product_slug=product,
                iso_week=iso_week,
                dry_run=dry_run,
                draft_only=draft_only,
            )
        except PhaseFailure as exc:
            typer.echo(
                f"  Bootstrap failed: phase {exc.phase} — {exc.reason}",
                err=True,
            )
            worst_exit = max(worst_exit, 1)
            continue

        outcome = execute_pipeline(plan, cfg, audit_store)
        typer.echo(
            f"[backfill] {iso_week}: status={outcome.status} exit={outcome.exit_code}",
            err=True,
        )
        worst_exit = max(worst_exit, outcome.exit_code)

    typer.echo(
        f"[backfill] done — worst_exit={worst_exit}, weeks_processed={len(week_list)}",
        err=True,
    )
    raise typer.Exit(worst_exit)


def _expand_week_range(
    start_year: int, start_week: int, end_year: int, end_week: int
) -> list[str]:
    from datetime import timedelta
    from pulse.phase_0.core.runplan import parse_iso_week

    if (start_year, start_week) > (end_year, end_week):
        raise ValueError(
            f"Start week {start_year}-W{start_week:02d} is after "
            f"end week {end_year}-W{end_week:02d}. Range must be ascending."
        )
    result: list[str] = []
    current = _fromisocalendar(start_year, start_week)
    end = _fromisocalendar(end_year, end_week)
    while current <= end:
        y, w, _ = current.isocalendar()
        result.append(f"{y}-W{w:02d}")
        current += timedelta(weeks=1)
    return result


def _fromisocalendar(year: int, week: int) -> "datetime":
    from datetime import datetime
    return datetime.fromisocalendar(year, week, 1)


# ---------------------------------------------------------------------------
# pulse debug ingest
# ---------------------------------------------------------------------------


@debug_app.command("ingest")
def debug_ingest(
    product: Annotated[str, typer.Option("--product", "-p")],
    week: Annotated[Optional[str], typer.Option("--week", "-w")] = None,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output JSONL path. Default: .pulse/runs/<run_id>/raw.jsonl"),
    ] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Run Phase 1 (ingest) and write raw reviews to JSONL for inspection."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    try:
        plan = bootstrap(
            config=cfg,
            product_slug=product,
            iso_week=week,
            dry_run=True,
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    from pulse.phase_1.ingestion import ingest

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Ingest failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]

    dest = out or _run_dir(str(plan.run_id)) / "raw.jsonl"
    with open(dest, "w", encoding="utf-8") as fh:
        for r in raw_reviews:
            fh.write(r.model_dump_json() + "\n")

    typer.echo(
        f"Wrote {len(raw_reviews)} raw reviews → {dest}\n"
        f"Sources: "
        + ", ".join(f"{src}={len(res.reviews)}" for src, res in ingest_results.items())
    )


# ---------------------------------------------------------------------------
# pulse debug clean
# ---------------------------------------------------------------------------


@debug_app.command("clean")
def debug_clean(
    product: Annotated[str, typer.Option("--product", "-p")],
    week: Annotated[Optional[str], typer.Option("--week", "-w")] = None,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output JSONL path. Default: .pulse/runs/<run_id>/clean.jsonl"),
    ] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Run Phase 1+2 (ingest + clean) and write CleanReview JSONL for inspection."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    try:
        plan = bootstrap(
            config=cfg,
            product_slug=product,
            iso_week=week,
            dry_run=True,
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    from pulse.phase_1.ingestion import ingest
    from pulse.phase_2.preprocess import clean

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Ingest failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]
    clean_reviews, stats = clean(raw_reviews)

    dest = out or _run_dir(str(plan.run_id)) / "clean.jsonl"
    with open(dest, "w", encoding="utf-8") as fh:
        for r in clean_reviews:
            fh.write(r.model_dump_json() + "\n")

    typer.echo(
        f"Wrote {len(clean_reviews)} clean reviews → {dest}\n"
        f"total_in={stats.total_in}  total_out={stats.total_out}  "
        f"dropped_short={stats.dropped_short}  dropped_lang={stats.dropped_lang}  "
        f"dedup={stats.dedup_count}"
    )


# ---------------------------------------------------------------------------
# pulse debug theme
# ---------------------------------------------------------------------------


@debug_app.command("theme")
def debug_theme(
    product: Annotated[str, typer.Option("--product", "-p")],
    week: Annotated[Optional[str], typer.Option("--week", "-w")] = None,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Output JSON path. Default: .pulse/runs/<run_id>/themes.json"),
    ] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Run Phase 1-4 (ingest → clean → cluster → theme) and write themes.json."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    try:
        plan = bootstrap(config=cfg, product_slug=product, iso_week=week, dry_run=True)
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    from pulse.phase_1.ingestion import ingest
    from pulse.phase_2.preprocess import clean
    from pulse.phase_3.cluster import cluster_reviews
    from pulse.phase_3.cluster.embed import SentenceTransformerEmbedder
    from pulse.llm import theme_clusters
    from pulse.llm.budget import Budget

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Ingest failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]
    clean_reviews, stats = clean(raw_reviews)
    typer.echo(
        f"[phase2] {len(clean_reviews)} clean reviews "
        f"(total_in={stats.total_in}  dropped_short={stats.dropped_short}  "
        f"dropped_lang={stats.dropped_lang}  dedup={stats.dedup_count})",
        err=True,
    )

    run_dir = _run_dir(str(plan.run_id))
    cache_path = run_dir / "embed_cache.db"
    embedder = SentenceTransformerEmbedder()
    try:
        clustering = asyncio.run(cluster_reviews(clean_reviews, embedder, cache_path))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"[phase3] {len(clustering.clusters)} clusters (fallback={clustering.fallback_used})",
        err=True,
    )

    reviews_by_id = {r.review_id: r for r in clean_reviews}
    budget = Budget(200_000)
    try:
        themes = asyncio.run(
            theme_clusters(clustering.clusters, reviews_by_id, budget, plan.llm_model)
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    dest = out or run_dir / "themes.json"
    with open(dest, "w", encoding="utf-8") as fh:
        import json as _json
        _json.dump([t.model_dump() for t in themes], fh, indent=2)

    typer.echo(
        f"Wrote {len(themes)} themes (budget_used={budget.used}) → {dest}"
    )


# ---------------------------------------------------------------------------
# pulse debug compose
# ---------------------------------------------------------------------------


@debug_app.command("compose")
def debug_compose(
    product: Annotated[str, typer.Option("--product", "-p")],
    week: Annotated[Optional[str], typer.Option("--week", "-w")] = None,
    out_dir: Annotated[
        Optional[Path],
        typer.Option("--out-dir", "-o", help="Output directory. Default: .pulse/runs/<run_id>/"),
    ] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Run Phase 1-5 (ingest → clean → cluster → theme → compose) and write artifacts."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    try:
        plan = bootstrap(config=cfg, product_slug=product, iso_week=week, dry_run=True)
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    from pulse.phase_1.ingestion import ingest
    from pulse.phase_2.preprocess import clean
    from pulse.phase_3.cluster import cluster_reviews
    from pulse.phase_3.cluster.embed import SentenceTransformerEmbedder
    from pulse.llm import theme_clusters
    from pulse.llm.budget import Budget
    from pulse.phase_5 import compose

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Ingest failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]
    clean_reviews, corpus_stats = clean(raw_reviews)
    typer.echo(f"[phase2] {len(clean_reviews)} clean reviews", err=True)

    run_dir = out_dir or _run_dir(str(plan.run_id))
    cache_path = Path(run_dir) / "embed_cache.db"
    embedder = SentenceTransformerEmbedder()
    try:
        clustering = asyncio.run(cluster_reviews(clean_reviews, embedder, cache_path))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[phase3] {len(clustering.clusters)} clusters", err=True)

    reviews_by_id = {r.review_id: r for r in clean_reviews}
    budget = Budget(200_000)
    try:
        themes = asyncio.run(
            theme_clusters(clustering.clusters, reviews_by_id, budget, plan.llm_model)
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[phase4] {len(themes)} themes", err=True)

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
        raise typer.Exit(1)

    dest = Path(run_dir)
    import json as _json

    doc_path = dest / "doc_report.json"
    doc_path.write_text(_json.dumps(doc_report.model_dump(), indent=2), encoding="utf-8")

    html_path = dest / "email.html"
    txt_path = dest / "email.txt"
    html_path.write_text(email_report.html_body, encoding="utf-8")
    txt_path.write_text(email_report.text_body, encoding="utf-8")

    typer.echo(
        f"[phase5] {len(doc_report.blocks)} doc blocks (anchor={doc_report.anchor})\n"
        f"  → {doc_path}\n"
        f"  → {html_path}\n"
        f"  → {txt_path}"
    )


# ---------------------------------------------------------------------------
# pulse debug deliver
# ---------------------------------------------------------------------------


@debug_app.command("deliver")
def debug_deliver(
    product: Annotated[str, typer.Option("--product", "-p")],
    week: Annotated[Optional[str], typer.Option("--week", "-w")] = None,
    draft_only: Annotated[Optional[bool], typer.Option("--draft-only/--no-draft-only")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run")] = False,
    out_dir: Annotated[
        Optional[Path],
        typer.Option("--out-dir", "-o", help="Output directory. Default: .pulse/runs/<run_id>/"),
    ] = None,
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Run Phase 1-6 (ingest → compose → MCP deliver) and write artifacts."""
    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    effective_draft_only = draft_only if draft_only is not None else (cfg.pulse_env != "prod")
    try:
        plan = bootstrap(
            config=cfg,
            product_slug=product,
            iso_week=week,
            dry_run=dry_run,
            draft_only=effective_draft_only,
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    from pulse.phase_1.ingestion import ingest
    from pulse.phase_2.preprocess import clean
    from pulse.phase_3.cluster import cluster_reviews
    from pulse.phase_3.cluster.embed import SentenceTransformerEmbedder
    from pulse.llm import theme_clusters
    from pulse.llm.budget import Budget
    from pulse.phase_5 import compose
    from pulse.phase_6 import deliver

    try:
        ingest_results = asyncio.run(ingest(plan, cfg))
    except PhaseFailure as exc:
        typer.echo(f"Ingest failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    raw_reviews = [r for res in ingest_results.values() for r in res.reviews]
    clean_reviews, corpus_stats = clean(raw_reviews)
    typer.echo(f"[phase2] {len(clean_reviews)} clean reviews", err=True)

    run_dir = out_dir or _run_dir(str(plan.run_id))
    cache_path = Path(run_dir) / "embed_cache.db"
    embedder = SentenceTransformerEmbedder()
    try:
        clustering = asyncio.run(cluster_reviews(clean_reviews, embedder, cache_path))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[phase3] {len(clustering.clusters)} clusters", err=True)

    reviews_by_id = {r.review_id: r for r in clean_reviews}
    budget = Budget(cfg.total_token_cap)
    try:
        themes = asyncio.run(
            theme_clusters(clustering.clusters, reviews_by_id, budget, plan.llm_model)
        )
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[phase4] {len(themes)} themes", err=True)

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
        raise typer.Exit(1)

    typer.echo(f"[phase5] {len(doc_report.blocks)} doc blocks (anchor={doc_report.anchor})", err=True)

    try:
        receipt = asyncio.run(deliver(plan, doc_report, email_report))
    except PhaseFailure as exc:
        typer.echo(f"Phase {exc.phase} failed: {exc.reason}", err=True)
        raise typer.Exit(1)

    dest = Path(run_dir)
    import json as _json

    receipt_path = dest / "receipt.json"
    receipt_path.write_text(
        _json.dumps(receipt.model_dump(), indent=2, default=str), encoding="utf-8"
    )
    typer.echo(
        f"[phase6] doc_status={receipt.doc_status} email_status={receipt.email_status}\n"
        f"  → {receipt_path}"
    )


# ---------------------------------------------------------------------------
# pulse audit show / list
# ---------------------------------------------------------------------------


def _audit_db() -> Path:
    return get_pulse_dir() / "audit.db"


@audit_app.command("show")
def audit_show(
    run_id: Annotated[str, typer.Argument(help="run_id to display.")],
) -> None:
    """Show details for a specific run."""
    from uuid import UUID

    from pulse.phase_7 import AuditStore

    store = AuditStore(_audit_db())
    try:
        store.migrate()
    except RuntimeError as exc:
        typer.echo(f"Audit DB error: {exc}", err=True)
        raise typer.Exit(1)

    try:
        uid = UUID(run_id)
    except ValueError:
        typer.echo(f"Invalid run_id: {run_id}", err=True)
        raise typer.Exit(64)

    record = store.get(uid)
    if record is None:
        typer.echo(f"No audit record found for run_id={run_id}", err=True)
        raise typer.Exit(1)

    lines = [
        f"run_id          : {record.run_id}",
        f"product         : {record.product}",
        f"iso_week        : {record.iso_week}",
        f"status          : {record.status}",
        f"started_at      : {record.started_at}",
        f"ended_at        : {record.ended_at or '—'}",
        f"dry_run         : {record.dry_run}",
        f"forced          : {record.forced}",
        f"llm_model       : {record.llm_model or '—'}",
        f"total_tokens    : {record.total_tokens or '—'}",
        f"cluster_count   : {record.cluster_count or '—'}",
        f"theme_count     : {record.theme_count or '—'}",
        f"doc_id          : {record.doc_id or '—'}",
        f"doc_anchor      : {record.doc_section_anchor or '—'}",
        f"doc_revision    : {record.doc_revision_id or '—'}",
        f"gmail_message   : {record.gmail_message_id or '—'}",
        f"gmail_draft     : {record.gmail_draft_id or '—'}",
        f"failed_phase    : {record.failed_phase or '—'}",
        f"error           : {record.error or '—'}",
    ]
    if record.corpus_stats:
        cs = record.corpus_stats
        lines.append(
            f"corpus          : in={cs.total_in} out={cs.total_out} "
            f"dropped_short={cs.dropped_short} dropped_lang={cs.dropped_lang} "
            f"dedup={cs.dedup_count}"
        )
    typer.echo("\n".join(lines))


@audit_app.command("list")
def audit_list(
    product: Annotated[Optional[str], typer.Option("--product", "-p")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """List recent runs, optionally filtered by product."""
    from pulse.phase_7 import AuditStore

    store = AuditStore(_audit_db())
    try:
        store.migrate()
    except RuntimeError as exc:
        typer.echo(f"Audit DB error: {exc}", err=True)
        raise typer.Exit(1)

    records = store.list(product=product, limit=limit)
    if not records:
        typer.echo("No audit records found.")
        return

    header = f"{'run_id':<36}  {'product':<12}  {'iso_week':<10}  {'status':<8}  {'started_at'}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for r in records:
        typer.echo(
            f"{str(r.run_id):<36}  {r.product:<12}  {r.iso_week:<10}  "
            f"{r.status:<8}  {r.started_at.isoformat()}"
        )


# ---------------------------------------------------------------------------
# pulse mcp probe
# ---------------------------------------------------------------------------


@mcp_app.command("probe")
def mcp_probe(
    config: Annotated[Optional[Path], typer.Option("--config", "-c")] = None,
    json_logs: Annotated[bool, typer.Option("--json-logs/--no-json-logs")] = False,
) -> None:
    """Connect to configured MCP servers and verify the required tools are present."""
    from pulse.phase_0.mcp import client as mcp_client

    obs.configure(json=json_logs)
    config_path = _resolve_config_path(config)

    try:
        cfg = load_config(config_path)
    except (FileNotFoundError, ValidationError, ValueError) as exc:
        typer.echo(f"Config error: {exc}", err=True)
        raise typer.Exit(64)

    typer.echo("Probing MCP servers …")
    try:
        asyncio.run(
            mcp_client.probe(
                docs_url=str(cfg.mcp.docs_url),
                gmail_url=str(cfg.mcp.gmail_url),
                required_docs_tools=list(cfg.mcp.required_docs_tools),
                required_gmail_tools=list(cfg.mcp.required_gmail_tools),
                timeout=cfg.mcp.probe_timeout_seconds,
            )
        )
        typer.echo("✓ Both MCP servers are healthy and expose the required tools.")
    except PhaseFailure as exc:
        typer.echo(f"✗ MCP probe failed: {exc.reason}", err=True)
        raise typer.Exit(69)  # EX_UNAVAILABLE


# ---------------------------------------------------------------------------
# pulse dashboard
# ---------------------------------------------------------------------------


@app.command()
def dashboard(
    port: Annotated[int, typer.Option("--port", help="Port to listen on.")] = 8000,
    host: Annotated[str, typer.Option("--host", help="Host to bind to.")] = "0.0.0.0",
) -> None:
    """Start the Pulse stakeholder dashboard in a browser."""
    import uvicorn
    from pulse.dashboard.server import app as dash_app

    typer.echo(f"Dashboard → http://localhost:{port}")
    uvicorn.run(dash_app, host=host, port=port)
