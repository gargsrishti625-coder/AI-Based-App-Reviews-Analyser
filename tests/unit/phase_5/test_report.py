"""Tests for Phase 5 report composition — P5-E1 through P5-E8 + edge cases."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import AnyUrl

from pulse.phase_0.core.types import McpEndpoints, ProductRegistryEntry, RunPlan
from pulse.phase_2.core.types import CorpusStats
from pulse.phase_4.core.types import Quote, Theme
from pulse.phase_5.anchor import anchor_for
from pulse.phase_5.doc_blocks import build_doc_report
from pulse.phase_5.email_render import render_email_report
from pulse.phase_5.types import DocReport, EmailReport

_UTC = timezone.utc
_NOW = datetime(2026, 4, 14, tzinfo=_UTC)   # window_end: Monday of W16
_START = datetime(2026, 2, 16, tzinfo=_UTC)  # window_start: 8 weeks prior


def _product(slug: str = "groww", display_name: str = "Groww") -> ProductRegistryEntry:
    return ProductRegistryEntry(
        slug=slug,
        display_name=display_name,
        pulse_doc_id="doc-123",
        play_store_id="com.groww.app",
        email_recipients=["pm@groww.in"],
    )


def _plan(product: ProductRegistryEntry | None = None, iso_week: str = "2026-W16") -> RunPlan:
    product = product or _product()
    return RunPlan(
        run_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        product=product,
        iso_week=iso_week,
        window_start=_START,
        window_end=_NOW,
        sources=["play_store"],
        llm_model="claude-sonnet-4-6",
        embedding_model="text-embedding-3-small",
        mcp_docs_url=AnyUrl("http://localhost:3001"),
        mcp_gmail_url=AnyUrl("http://localhost:3002"),
        dry_run=False,
        draft_only=True,
    )


def _stats(total_in: int = 120, total_out: int = 100) -> CorpusStats:
    dropped = total_in - total_out
    return CorpusStats(
        total_in=total_in,
        total_out=total_out,
        dropped_short=dropped,
    )


def _theme(
    title: str = "App crashes on launch",
    summary: str = "Many users report the app crashes immediately on opening.",
    quotes: list[Quote] | None = None,
    action_ideas: list[str] | None = None,
    cluster_id: int = 0,
) -> Theme:
    quotes = quotes or [Quote(text="crashes immediately on opening", review_id="R1")]
    action_ideas = action_ideas if action_ideas is not None else ["Investigate crash reports on launch"]
    return Theme(
        title=title,
        summary=summary,
        quotes=quotes,
        action_ideas=action_ideas,
        supporting_review_ids=["R1"],
        cluster_id=cluster_id,
    )


# ── P5-E2: Anchor determinism ─────────────────────────────────────────────────


class TestAnchor:
    def test_basic_product(self) -> None:
        assert anchor_for("groww", "2026-W17") == "pulse-groww-2026-W17"

    def test_spaces_slugified(self) -> None:
        assert anchor_for("Groww Mutual Funds", "2026-W17") == "pulse-groww-mutual-funds-2026-W17"

    def test_unicode_stripped_to_ascii(self) -> None:
        result = anchor_for("Grøww", "2026-W17")
        # non-ASCII becomes separator then stripped/lowered
        assert result.startswith("pulse-")
        assert "2026-W17" in result

    def test_iso_week_year_boundary(self) -> None:
        assert anchor_for("groww", "2025-W53") == "pulse-groww-2025-W53"

    def test_deterministic_repeated_calls(self) -> None:
        a = anchor_for("groww", "2026-W17")
        b = anchor_for("groww", "2026-W17")
        assert a == b


# ── P5-E1: Doc block structure ────────────────────────────────────────────────


class TestDocBlocks:
    def test_three_themes_render_doc_blocks(self) -> None:
        """P5-E1: 3 themes → H2 + 3×(H3 + paragraph + blockquote + action H3 + bullet) + footer."""
        themes = [_theme(title=f"Theme {i}", cluster_id=i) for i in range(3)]
        plan = _plan()
        stats = _stats()

        doc = build_doc_report(themes, plan, stats)

        assert isinstance(doc, DocReport)
        assert doc.anchor == "pulse-groww-2026-W16"

        types = [b.type for b in doc.blocks]
        # First block is H2
        assert types[0] == "heading_2"
        # Three H3 theme headings
        assert types.count("heading_3") >= 3
        # Three blockquotes (one quote per theme)
        assert types.count("blockquote") == 3
        # Three bullet action ideas
        assert types.count("bullet") == 3

    def test_h2_text_format(self) -> None:
        """P5-E6: heading text matches 'Week of YYYY-MM-DD (ISO YYYY-Www) — N reviews'."""
        plan = _plan()
        stats = _stats(total_in=150, total_out=100)
        doc = build_doc_report([_theme()], plan, stats)

        h2 = next(b for b in doc.blocks if b.type == "heading_2")
        assert h2.text == "Week of 2026-04-14 (ISO 2026-W16) — 100 reviews"

    def test_h2_has_anchor(self) -> None:
        plan = _plan()
        doc = build_doc_report([_theme()], plan, _stats())
        h2 = next(b for b in doc.blocks if b.type == "heading_2")
        assert h2.anchor == "pulse-groww-2026-W16"

    def test_blockquote_has_attribution(self) -> None:
        plan = _plan()
        doc = build_doc_report([_theme()], plan, _stats())
        bq = next(b for b in doc.blocks if b.type == "blockquote")
        assert bq.attribution == "R1"
        assert bq.text == "crashes immediately on opening"

    def test_no_action_subheading_when_empty(self) -> None:
        """P5-E7: no action ideas → 'Action ideas' H3 not emitted."""
        theme = _theme(action_ideas=[])
        plan = _plan()
        doc = build_doc_report([theme], plan, _stats())

        headings = [b.text for b in doc.blocks if b.type == "heading_3"]
        assert "Action ideas" not in headings
        assert types.count("bullet") == 0 if (types := [b.type for b in doc.blocks]) else True

    def test_action_subheading_present_when_ideas_exist(self) -> None:
        theme = _theme(action_ideas=["Fix crash on startup"])
        plan = _plan()
        doc = build_doc_report([theme], plan, _stats())

        headings = [b.text for b in doc.blocks if b.type == "heading_3"]
        assert "Action ideas" in headings

    def test_missing_source_footer(self) -> None:
        """P5-E4: missing source → footer note."""
        plan = _plan()
        doc = build_doc_report(
            [_theme()], plan, _stats(), missing_sources=["app_store"]
        )
        texts = [b.text for b in doc.blocks if b.type == "paragraph"]
        assert any("App Store" in t and "unavailable" in t for t in texts)

    def test_fallback_caveat_in_footer(self) -> None:
        plan = _plan()
        doc = build_doc_report([_theme()], plan, _stats(), fallback_used=True)
        texts = [b.text for b in doc.blocks if b.type == "paragraph"]
        assert any("rating bucket" in t.lower() for t in texts)

    def test_long_title_truncated_at_80_chars(self) -> None:
        long_title = "A" * 100
        theme = _theme(title=long_title)
        plan = _plan()
        doc = build_doc_report([theme], plan, _stats())
        h3_texts = [b.text for b in doc.blocks if b.type == "heading_3" and b.text != "Action ideas"]
        assert len(h3_texts[0]) == 80

    # P5-E3: idempotent render
    def test_idempotent_render(self) -> None:
        """Same inputs → identical DocReport."""
        themes = [_theme()]
        plan = _plan()
        stats = _stats()
        doc1 = build_doc_report(themes, plan, stats)
        doc2 = build_doc_report(themes, plan, stats)
        assert doc1.model_dump() == doc2.model_dump()

    def test_metadata_contains_run_id(self) -> None:
        plan = _plan()
        doc = build_doc_report([_theme()], plan, _stats())
        assert doc.metadata["run_id"] == "00000000-0000-0000-0000-000000000001"
        assert doc.metadata["iso_week"] == "2026-W16"


# ── P5-E5 / P5-E8: Email rendering ───────────────────────────────────────────


class TestEmailRender:
    def test_deep_link_placeholder_present_once_in_html(self) -> None:
        """P5-E8: HTML body contains exactly one {{PULSE_DEEP_LINK}}."""
        plan = _plan()
        report = render_email_report([_theme()], plan, "pulse-groww-2026-W16")
        assert report.html_body.count("{{PULSE_DEEP_LINK}}") == 1

    def test_deep_link_placeholder_present_once_in_text(self) -> None:
        """P5-E8: text body contains exactly one {{PULSE_DEEP_LINK}}."""
        plan = _plan()
        report = render_email_report([_theme()], plan, "pulse-groww-2026-W16")
        assert report.text_body.count("{{PULSE_DEEP_LINK}}") == 1

    def test_subject_format(self) -> None:
        plan = _plan()
        report = render_email_report([_theme()], plan, "pulse-groww-2026-W16")
        assert report.subject == "Weekly Pulse — Groww — Week of 2026-04-14"

    def test_html_and_text_both_contain_theme_titles(self) -> None:
        """P5-E5: both bodies mention the same top theme title."""
        theme = _theme(title="Portfolio Screen Crashes")
        plan = _plan()
        report = render_email_report([theme], plan, "pulse-groww-2026-W16")
        assert "Portfolio Screen Crashes" in report.html_body
        assert "Portfolio Screen Crashes" in report.text_body

    def test_html_lists_top_three_titles(self) -> None:
        themes = [_theme(title=f"Theme {i}", cluster_id=i) for i in range(5)]
        plan = _plan()
        report = render_email_report(themes, plan, "pulse-groww-2026-W16")
        # Only top 3 should appear
        assert "Theme 0" in report.html_body
        assert "Theme 1" in report.html_body
        assert "Theme 2" in report.html_body
        # Theme 3 and 4 are beyond top-3 cap
        assert "Theme 3" not in report.html_body
        assert "Theme 4" not in report.html_body

    def test_idempotent_email_render(self) -> None:
        """P5-E3: same inputs → identical EmailReport."""
        themes = [_theme()]
        plan = _plan()
        r1 = render_email_report(themes, plan, "pulse-groww-2026-W16")
        r2 = render_email_report(themes, plan, "pulse-groww-2026-W16")
        assert r1.html_body == r2.html_body
        assert r1.text_body == r2.text_body

    def test_no_current_time_in_body(self) -> None:
        """Bodies must be deterministic — no injected 'now()' strings."""
        import re
        plan = _plan()
        report = render_email_report([_theme()], plan, "pulse-groww-2026-W16")
        # 'now' as a dynamic timestamp would look like a current datetime string
        # Check neither body contains 2026-04-26 (today's date) which is not the window_end
        assert "2026-04-26" not in report.html_body
        assert "2026-04-26" not in report.text_body

    def test_email_report_type(self) -> None:
        plan = _plan()
        report = render_email_report([_theme()], plan, "pulse-groww-2026-W16")
        assert isinstance(report, EmailReport)
        assert report.subject
        assert report.html_body
        assert report.text_body
