from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from pulse.phase_0.core.clock import now
from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.types import ProductRegistryEntry, PulseConfig, RunPlan
from pulse.phase_0.mcp import client as mcp_client
from pulse.phase_0.obs.logger import bind_phase, bind_run_context, get_logger

log = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


# ---------------------------------------------------------------------------
# ISO-week helpers
# ---------------------------------------------------------------------------


def parse_iso_week(s: str) -> tuple[int, int]:
    """Parse 'YYYY-Www' into (year, week).  Raises ValueError on bad format or
    invalid week number for the given year."""
    m = _ISO_WEEK_RE.match(s)
    if not m:
        raise ValueError(
            f"Invalid ISO week format: {s!r}. Expected YYYY-Www (e.g. 2026-W17)."
        )
    year, week = int(m.group(1)), int(m.group(2))
    if week < 1 or week > 53:
        raise ValueError(f"Week number must be between 01 and 53, got {week}.")
    # datetime.fromisocalendar raises ValueError if the combination is invalid
    # (e.g. 2025-W53 when 2025 only has 52 weeks).
    try:
        datetime.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"Week {week:02d} does not exist in {year}: {exc}") from exc
    return year, week


def last_completed_iso_week_ist() -> str:
    """Return the ISO week string for the last *fully completed* week, computed in IST.

    'Last completed' means the week that ended before today's Monday 00:00 IST.
    Subtracting 7 days from now-in-IST is a reliable way to land in the previous week
    regardless of which day of the week this function is called.
    """
    now_ist = datetime.now(tz=IST)
    last_week_ist = now_ist - timedelta(days=7)
    year, week, _ = last_week_ist.isocalendar()
    return f"{year}-W{week:02d}"


def iso_week_to_window(iso_week: str, window_weeks: int) -> tuple[datetime, datetime]:
    """Return (window_start, window_end) as UTC datetimes for the given ISO week.

    window_end  = end of Sunday of iso_week (23:59:59.999999 UTC).
    window_start = window_end minus (window_weeks * 7 days), at 00:00:00 UTC.
    """
    year, week = parse_iso_week(iso_week)
    # ISO week ends on Sunday (day 7)
    sunday = datetime.fromisocalendar(year, week, 7)
    window_end = sunday.replace(
        hour=23, minute=59, second=59, microsecond=999_999, tzinfo=timezone.utc
    )
    window_start = (window_end - timedelta(weeks=window_weeks)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return window_start, window_end


# ---------------------------------------------------------------------------
# RunPlan construction
# ---------------------------------------------------------------------------


def _resolve_sources(product: ProductRegistryEntry) -> list[Literal["app_store", "play_store"]]:
    sources: list[Literal["app_store", "play_store"]] = []
    if product.app_store_id:
        sources.append("app_store")
    if product.play_store_id:
        sources.append("play_store")
    return sources


def build_runplan(
    *,
    config: PulseConfig,
    product_slug: str,
    iso_week: str | None = None,
    dry_run: bool = False,
    draft_only: bool | None = None,
    force_resend: bool = False,
    run_id: UUID | None = None,
) -> RunPlan:
    """Assemble and return a frozen RunPlan.  Does NOT probe MCP servers — call
    ``bootstrap()`` for the full Phase 0 flow including probing.

    Validates:
    - product exists in registry
    - ISO week parses and has a valid window (fully in the past)
    - at least one source (app_store or play_store) is configured for the product

    Raises PhaseFailure(0) on any validation error.
    """
    # 1. Resolve product
    if product_slug not in config.products:
        known = sorted(config.products.keys())
        raise PhaseFailure(
            0,
            f"Unknown product {product_slug!r}. Known products: {known}",
        )
    product = config.products[product_slug]

    # 2. Resolve ISO week
    resolved_week = iso_week or last_completed_iso_week_ist()
    try:
        window_start, window_end = iso_week_to_window(resolved_week, config.window_weeks)
    except ValueError as exc:
        raise PhaseFailure(0, f"Invalid week {resolved_week!r}: {exc}") from exc

    # 3. Window must be fully in the past
    current = now()
    if window_end > current:
        raise PhaseFailure(
            0,
            f"ISO week {resolved_week!r} is not fully in the past "
            f"(window ends {window_end.isoformat()}, now is {current.isoformat()}).",
        )

    # 4. Sources
    sources = _resolve_sources(product)
    if not sources:
        raise PhaseFailure(
            0,
            f"Product {product_slug!r} has neither app_store_id nor play_store_id configured.",
        )

    # 5. draft_only: default True for non-prod environments
    if draft_only is None:
        draft_only = config.pulse_env in {"dev", "staging"}

    # 6. Warn if GOOGLE_OAUTH_TOKEN is in env (architectural invariant)
    if os.environ.get("GOOGLE_OAUTH_TOKEN") or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        log.warning(
            "google_token_in_agent_env",
            message=(
                "Google OAuth token detected in agent environment. "
                "Tokens should live in the MCP servers' config, not the agent."
            ),
        )

    return RunPlan(
        run_id=run_id or uuid4(),
        product=product,
        iso_week=resolved_week,
        window_start=window_start,
        window_end=window_end,
        sources=sources,
        llm_model=config.llm_model,
        embedding_model=config.embedding_model,
        mcp_docs_url=config.mcp.docs_url,
        mcp_gmail_url=config.mcp.gmail_url,
        dry_run=dry_run,
        draft_only=draft_only,
        force_resend=force_resend,
    )


# ---------------------------------------------------------------------------
# Bootstrap (Phase 0 orchestrator)
# ---------------------------------------------------------------------------


def bootstrap(
    *,
    config: PulseConfig,
    product_slug: str,
    iso_week: str | None = None,
    dry_run: bool = False,
    draft_only: bool | None = None,
    force_resend: bool = False,
    run_id: UUID | None = None,
    skip_mcp_probe: bool = False,
) -> RunPlan:
    """Full Phase 0 entry point.

    1. Build and freeze a RunPlan.
    2. Bind structlog context.
    3. Probe MCP servers (unless skip_mcp_probe=True, which is only for tests).

    Returns a frozen RunPlan.  Raises PhaseFailure(0) on any failure.
    """
    bind_phase(0)

    plan = build_runplan(
        config=config,
        product_slug=product_slug,
        iso_week=iso_week,
        dry_run=dry_run,
        draft_only=draft_only,
        force_resend=force_resend,
        run_id=run_id,
    )

    bind_run_context(
        run_id=str(plan.run_id),
        product=plan.product.slug,
        iso_week=plan.iso_week,
    )

    log.info(
        "phase_0_plan_built",
        run_id=str(plan.run_id),
        product=plan.product.slug,
        iso_week=plan.iso_week,
        window_start=plan.window_start.isoformat(),
        window_end=plan.window_end.isoformat(),
        sources=plan.sources,
        dry_run=plan.dry_run,
        draft_only=plan.draft_only,
    )

    if not skip_mcp_probe and not plan.dry_run:
        asyncio.run(
            mcp_client.probe(
                docs_url=str(plan.mcp_docs_url),
                gmail_url=str(plan.mcp_gmail_url),
                required_docs_tools=list(config.mcp.required_docs_tools),
                required_gmail_tools=list(config.mcp.required_gmail_tools),
                timeout=config.mcp.probe_timeout_seconds,
            )
        )

    log.info("phase_0_complete", run_id=str(plan.run_id))
    return plan
