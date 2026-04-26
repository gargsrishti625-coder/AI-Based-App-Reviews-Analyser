"""Render EmailReport from themes using Jinja2 templates."""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pulse.phase_5.types import EmailReport

if TYPE_CHECKING:
    from pulse.phase_4.core.types import Theme
    from pulse.phase_0.core.types import RunPlan

log = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_DEEP_LINK_SENTINEL = "{{PULSE_DEEP_LINK}}"
_MAX_TOP_TITLES = 3
_MAX_HTML_BYTES = 50_000


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )


def render_email_report(
    themes: list[Theme],
    plan: RunPlan,
    anchor: str,
) -> EmailReport:
    """Render HTML and plain-text email bodies from validated themes.

    The rendered bodies contain exactly one ``{{PULSE_DEEP_LINK}}`` sentinel
    each; Phase 6 performs a simple string replace to inject the real URL.
    """
    t0 = time.monotonic()

    subject = (
        f"Weekly Pulse — {plan.product.display_name} — "
        f"Week of {plan.window_end:%Y-%m-%d}"
    )

    # Cap to top-3 titles for the teaser
    top_titles = [t.title for t in themes[:_MAX_TOP_TITLES]]

    window_str = f"{plan.window_start:%Y-%m-%d} → {plan.window_end:%Y-%m-%d}"
    footer_meta = (
        f"LLM: {plan.llm_model} · "
        f"Window: {window_str} · "
        f"Sources: {', '.join(plan.sources)}"
    )

    ctx = {
        "subject": subject,
        "top_titles": top_titles,
        "footer_meta": footer_meta,
        "anchor": anchor,
    }

    env = _jinja_env()
    html_body = env.get_template("email.html.j2").render(**ctx)
    text_body = env.get_template("email.txt.j2").render(**ctx)

    # Cap HTML size to keep the email a teaser not a copy of the Doc
    if len(html_body.encode()) > _MAX_HTML_BYTES:
        ctx["top_titles"] = top_titles[:1]
        html_body = env.get_template("email.html.j2").render(**ctx)
        log.warning("phase_5_email_html_truncated", html_bytes=len(html_body.encode()))

    _assert_single_placeholder(html_body, "HTML")
    _assert_single_placeholder(text_body, "text")

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    log.info(
        "phase_5_email_rendered",
        email_html_bytes=len(html_body.encode()),
        email_text_bytes=len(text_body.encode()),
        email_render_duration_ms=elapsed_ms,
    )

    return EmailReport(subject=subject, html_body=html_body, text_body=text_body)


def _assert_single_placeholder(body: str, label: str) -> None:
    count = body.count(_DEEP_LINK_SENTINEL)
    if count != 1:
        raise ValueError(
            f"EmailReport {label} body must contain exactly one "
            f"'{_DEEP_LINK_SENTINEL}' sentinel; found {count}."
        )
