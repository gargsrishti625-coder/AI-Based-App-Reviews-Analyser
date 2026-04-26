"""Weekly scheduler entry point.

Invoked by cron / GitHub Actions on Monday 06:00 IST. Resolves the
last-completed ISO week in IST, iterates the product registry in
alphabetical slug order, and runs the pipeline for each product
sequentially. One product's failure does not block the rest; the process
exits with the worst exit code observed.

Usage:
    python -m pulse.scheduler.weekly --config config/pulse.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import typer

from pulse.phase_0.config.loader import load_config
from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.runplan import bootstrap, last_completed_iso_week_ist
from pulse.phase_0.obs import logger as obs
from pulse.util.paths import get_pulse_dir
from pulse.phase_7 import AuditStore
from pulse.scheduler.pipeline import PipelineOutcome, execute_pipeline

_DEFAULT_CONFIG = Path("config/pulse.yaml")


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pulse.scheduler.weekly",
        description="Run the weekly pulse for every product in the registry.",
    )
    p.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help="Path to YAML config file (default: %(default)s).",
    )
    p.add_argument(
        "--week",
        default=None,
        help=(
            "ISO week to run, e.g. 2026-W17. "
            "Defaults to the last completed week in IST."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Build plans and log them; skip all MCP writes.",
    )
    return p


def _run_one(
    cfg, product_slug: str, iso_week: str, dry_run: bool, audit_store: AuditStore
) -> PipelineOutcome:
    try:
        plan = bootstrap(
            config=cfg,
            product_slug=product_slug,
            iso_week=iso_week,
            dry_run=dry_run,
            draft_only=None,  # use config default; non-prod → drafts
        )
    except PhaseFailure as exc:
        typer.echo(
            f"[weekly] {product_slug}: phase {exc.phase} bootstrap failure — {exc.reason}",
            err=True,
        )
        # Return a synthetic failed outcome with a zero UUID so aggregation works.
        from uuid import UUID as _UUID

        return PipelineOutcome.failed(_UUID(int=0), exc.phase, exc.reason)

    return execute_pipeline(plan, cfg, audit_store)


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    obs.configure(json=True)

    try:
        cfg = load_config(args.config)
    except Exception as exc:  # noqa: BLE001 — config errors map to EX_CONFIG
        typer.echo(f"[weekly] config error: {exc}", err=True)
        return 64

    iso_week = args.week or last_completed_iso_week_ist()
    typer.echo(
        f"[weekly] iso_week={iso_week} products={sorted(cfg.products.keys())} "
        f"dry_run={args.dry_run}",
        err=True,
    )

    audit_db = get_pulse_dir() / "audit.db"
    audit_store = AuditStore(audit_db)
    audit_store.migrate()

    worst_exit = 0
    for slug in sorted(cfg.products.keys()):
        typer.echo(f"[weekly] === {slug} / {iso_week} ===", err=True)
        outcome = _run_one(cfg, slug, iso_week, args.dry_run, audit_store)
        typer.echo(
            f"[weekly] {slug}: status={outcome.status} exit={outcome.exit_code}",
            err=True,
        )
        worst_exit = max(worst_exit, outcome.exit_code)

    typer.echo(
        f"[weekly] done — worst_exit={worst_exit}, processed={len(cfg.products)} products",
        err=True,
    )
    return worst_exit


if __name__ == "__main__":
    sys.exit(main())
