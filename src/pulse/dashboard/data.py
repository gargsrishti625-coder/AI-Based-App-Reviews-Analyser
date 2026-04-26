"""Data-loading helpers for the dashboard.

Reads from .pulse/runs/<run_id>/ and .pulse/audit.db without importing
the full pipeline stack — keeps the dashboard import footprint small.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from pathlib import Path


def get_pulse_root() -> Path:
    """Project root — two levels up from this file (src/pulse/dashboard/)."""
    return Path(__file__).parent.parent.parent.parent


def get_audit_db() -> Path:
    return get_pulse_root() / ".pulse" / "audit.db"


def get_runs_dir() -> Path:
    return get_pulse_root() / ".pulse" / "runs"


def get_latest_run(product_slug: str) -> dict | None:
    """Return the most recent ok/partial run row for *product_slug*, or None."""
    db_path = get_audit_db()
    if not db_path.exists():
        return None
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            """
            SELECT run_id, product, iso_week, started_at, ended_at,
                   status, theme_count, cluster_count,
                   gmail_draft_id, gmail_message_id, doc_id
            FROM runs
            WHERE product = ? AND status IN ('ok', 'partial')
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (product_slug,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_recent_runs(limit: int = 10) -> list[dict]:
    """Return the last *limit* runs across all products."""
    db_path = get_audit_db()
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_stakeholder_stats(product_slug: str) -> dict:
    """Aggregate counts for the right-panel stats: drafts, pending, pulse level."""
    db_path = get_audit_db()
    if not db_path.exists():
        return {"drafts": 0, "pending": 0, "pulse": "Low"}
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        drafts = con.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE product = ? AND gmail_draft_id IS NOT NULL",
            (product_slug,),
        ).fetchone()["n"]
        pending = con.execute(
            "SELECT COUNT(*) AS n FROM runs WHERE product = ? AND status IN ('partial','failed')",
            (product_slug,),
        ).fetchone()["n"]
    finally:
        con.close()

    # Pulse level derived from latest run's themes (lower avg rating = higher pulse)
    latest = get_latest_run(product_slug)
    pulse = "Low"
    if latest:
        themes = load_themes_enriched(get_runs_dir() / latest["run_id"])
        ratings = [t["avg_rating"] for t in themes if t.get("avg_rating") is not None]
        if ratings:
            mean = sum(ratings) / len(ratings)
            pulse = "High" if mean < 3.5 else "Medium" if mean < 4.2 else "Low"

    return {"drafts": drafts, "pending": pending, "pulse": pulse}


def get_recent_activities(product_slug: str, limit: int = 3) -> list[dict]:
    """Return formatted activity entries for the Recent Activities widget."""
    db_path = get_audit_db()
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT iso_week, status, gmail_draft_id, doc_id, started_at
            FROM runs
            WHERE product = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (product_slug, limit),
        ).fetchall()
    finally:
        con.close()

    activities = []
    for r in rows:
        if r["gmail_draft_id"]:
            activities.append({
                "label": f"Drafted weekly pulse for {r['iso_week']}",
                "color": "#22c55e",
            })
        elif r["doc_id"]:
            activities.append({
                "label": f"Updated Pulse Doc for {r['iso_week']}",
                "color": "#22c55e",
            })
        else:
            activities.append({
                "label": f"Run {r['status']} for {r['iso_week']}",
                "color": "#9ca3af",
            })
    return activities


def get_quarter_label(iso_week: str | None) -> str:
    """Convert '2026-W16' → 'Q2 2026'."""
    if not iso_week or "-W" not in iso_week:
        return "Current Period"
    try:
        year, week = iso_week.split("-W")
        week_num = int(week)
        quarter = (week_num - 1) // 13 + 1
        quarter = min(quarter, 4)
        return f"Q{quarter} {year}"
    except (ValueError, IndexError):
        return "Current Period"


def compute_market_sentiment(themes: list[dict]) -> dict:
    """Compute overall sentiment direction from theme ratings."""
    ratings = [t["avg_rating"] for t in themes if t.get("avg_rating") is not None]
    if not ratings:
        return {"value": "0.0", "direction": "neutral", "label": "neutral"}
    mean = sum(ratings) / len(ratings)
    # Treat 3.5 as neutral baseline; +1.0 = +20% bullish
    delta = (mean - 3.5) * 20
    sign = "+" if delta >= 0 else ""
    label = "bullish" if delta >= 0 else "bearish"
    direction = "up" if delta >= 0 else "down"
    return {"value": f"{sign}{delta:.1f}%", "direction": direction, "label": label}


def load_themes_enriched(run_dir: Path) -> list[dict]:
    """Load themes.json and enrich each theme with avg_rating, review_count, priority_label."""
    themes_path = run_dir / "themes.json"
    clean_path = run_dir / "clean.jsonl"

    if not themes_path.exists():
        return []

    themes: list[dict] = json.loads(themes_path.read_text())

    # Build review_id → rating lookup from clean.jsonl
    ratings: dict[str, int] = {}
    if clean_path.exists():
        for line in clean_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            ratings[r["review_id"]] = r["rating"]

    # Enrich each theme
    for theme in themes:
        ids: list[str] = theme.get("supporting_review_ids", [])
        matched = [ratings[i] for i in ids if i in ratings]
        theme["review_count"] = len(ids)
        theme["avg_rating"] = round(statistics.mean(matched), 1) if matched else None

    # Sort by review_count descending, assign priority labels
    themes.sort(key=lambda t: t["review_count"], reverse=True)
    _PRIORITY_LABELS = ["Top Priority", "High Priority", "High Priority"]
    for i, theme in enumerate(themes):
        theme["priority_label"] = _PRIORITY_LABELS[i] if i < len(_PRIORITY_LABELS) else "Priority"
        theme["priority_color"] = (
            "#ef4444" if i == 0 else "#f97316" if i < 3 else "#3b82f6"
        )

    return themes


def load_receipt(run_dir: Path) -> dict:
    p = run_dir / "receipt.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_email_text(run_dir: Path) -> str:
    p = run_dir / "email.txt"
    if not p.exists():
        return ""
    raw = p.read_text()
    # Strip the unsubstituted deep-link placeholder so the Gmail compose body is clean
    return raw.replace("{{PULSE_DEEP_LINK}}", "").strip()


def load_email_subject(run_dir: Path) -> str:
    p = run_dir / "email.txt"
    if not p.exists():
        return ""
    first_line = p.read_text().splitlines()[0]
    return first_line.strip()
