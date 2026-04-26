"""Groww Pulse stakeholder dashboard — FastAPI backend.

Run with:
    pulse dashboard          # http://localhost:8000
    pulse dashboard --port 3000
"""
from __future__ import annotations

import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import yaml
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from pulse.dashboard.data import (
    compute_market_sentiment,
    get_latest_run,
    get_pulse_root,
    get_quarter_label,
    get_recent_activities,
    get_recent_runs,
    get_runs_dir,
    get_stakeholder_stats,
    load_email_subject,
    load_email_text,
    load_receipt,
    load_themes_enriched,
)

app = FastAPI(title="Groww Pulse Dashboard", docs_url=None, redoc_url=None)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Background trigger state: product_slug → "running" | None
_trigger_state: dict[str, str] = {}


# ── Config helper ──────────────────────────────────────────────────────────────

def _load_products() -> list[dict]:
    """Return list of product dicts from config/pulse.yaml (no env-var expansion needed)."""
    cfg_path = get_pulse_root() / "config" / "pulse.yaml"
    if not cfg_path.exists():
        return []
    raw = yaml.safe_load(cfg_path.read_text())
    products = []
    for slug, entry in raw.get("products", {}).items():
        if isinstance(entry, dict):
            products.append({
                "slug": slug,
                "display_name": entry.get("display_name", slug.title()),
                "email_recipients": entry.get("email_recipients", []),
            })
    return products


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    products = _load_products()
    if not products:
        return HTMLResponse("<h1>No products configured</h1>", status_code=500)

    # For now we show a single primary product (the first in the list).
    product = products[0]
    slug = product["slug"]
    run = get_latest_run(slug)

    themes: list[dict] = []
    receipt: dict = {}
    email_subject = ""
    email_body = ""
    run_id = None
    iso_week = None
    synced_at = None

    if run:
        run_id = run["run_id"]
        iso_week = run["iso_week"]
        run_dir = get_runs_dir() / run_id
        themes = load_themes_enriched(run_dir)
        receipt = load_receipt(run_dir)
        email_subject = load_email_subject(run_dir)
        email_body = load_email_text(run_dir)
        synced_at = run.get("ended_at") or run.get("started_at")

    # Primary stakeholder = first recipient in pulse.yaml
    primary_email = product["email_recipients"][0] if product["email_recipients"] else ""
    primary_name = _guess_team_name(primary_email) if primary_email else "Stakeholder"
    primary_initial = (primary_name[:1] or "?").upper()

    gmail_url = ""
    if primary_email and run_id:
        gmail_url = (
            "https://mail.google.com/mail/?view=cm"
            f"&to={quote(primary_email)}"
            f"&su={quote(email_subject)}"
            f"&body={quote(email_body[:1500])}"
        )

    # Mark themes as "urgent" if rating < 4.0 (used for the "N Urgent Items" badge)
    urgent_count = sum(
        1 for t in themes if t.get("avg_rating") is not None and t["avg_rating"] < 4.0
    )

    # Tag the priority strings the template will style
    for i, theme in enumerate(themes):
        theme["priority_color_class"] = "urgent" if i == 0 else "high"
        theme["priority_label_uc"] = theme["priority_label"].upper()

    stats = get_stakeholder_stats(slug)
    activities = get_recent_activities(slug, limit=3)
    sentiment = compute_market_sentiment(themes)
    quarter = get_quarter_label(iso_week)

    ctx = {
        "product_slug": slug,
        "display_name": product["display_name"],
        "run_id": run_id,
        "iso_week": iso_week,
        "synced_at": _format_dt(synced_at),
        "themes": themes,
        "urgent_count": urgent_count,
        "receipt": receipt,
        "primary_email": primary_email,
        "primary_name": primary_name,
        "primary_initial": primary_initial,
        "gmail_url": gmail_url,
        "print_url": f"/print/{run_id}" if run_id else "#",
        "trigger_state": _trigger_state.get(slug),
        "stats": stats,
        "activities": activities,
        "sentiment": sentiment,
        "quarter": quarter,
    }
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context=ctx,
    )


@app.get("/print/{run_id}", response_class=HTMLResponse)
async def print_report(request: Request, run_id: str) -> HTMLResponse:
    run_dir = get_runs_dir() / run_id
    if not run_dir.exists():
        return HTMLResponse("<h1>Run not found</h1>", status_code=404)

    themes = load_themes_enriched(run_dir)
    email_subject = load_email_subject(run_dir)

    return templates.TemplateResponse(
        request=request,
        name="print.html",
        context={"themes": themes, "subject": email_subject, "run_id": run_id},
    )


@app.get("/api/latest")
async def api_latest(product: str | None = None) -> JSONResponse:
    products = _load_products()
    results = []

    for p in products:
        if product and p["slug"] != product:
            continue
        slug = p["slug"]
        run = get_latest_run(slug)
        if not run:
            results.append({"product": slug, "run": None})
            continue
        run_id = run["run_id"]
        run_dir = get_runs_dir() / run_id
        themes = load_themes_enriched(run_dir)
        receipt = load_receipt(run_dir)
        results.append({
            "product": slug,
            "run_id": run_id,
            "iso_week": run["iso_week"],
            "synced_at": run.get("ended_at"),
            "theme_count": len(themes),
            "receipt": receipt,
            "trigger_state": _trigger_state.get(slug),
        })

    return JSONResponse(results if product is None else (results[0] if results else {}))


@app.post("/api/trigger")
async def api_trigger(body: dict) -> JSONResponse:
    slug = body.get("product", "")
    if not slug:
        return JSONResponse({"error": "product required"}, status_code=400)

    if _trigger_state.get(slug) == "running":
        return JSONResponse({"status": "already_running"})

    def _run() -> None:
        _trigger_state[slug] = "running"
        try:
            subprocess.run(
                ["pulse", "run", "--product", slug],
                cwd=str(get_pulse_root()),
                check=False,
                capture_output=True,
            )
        finally:
            _trigger_state[slug] = "done"

    threading.Thread(target=_run, daemon=True).start()
    return JSONResponse({"status": "triggered"})


@app.get("/api/runs")
async def api_runs() -> JSONResponse:
    return JSONResponse(get_recent_runs(10))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _format_dt(iso: str | None) -> str:
    if iso is None:
        return datetime.now(timezone.utc).strftime("%d/%m/%Y, %H:%M:%S")
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d/%m/%Y, %H:%M:%S")
    except Exception:
        return iso


def _guess_team_name(email: str) -> str:
    local = email.split("@")[0]
    parts = local.replace(".", " ").replace("_", " ").replace("-", " ").split()
    return " ".join(p.title() for p in parts)
