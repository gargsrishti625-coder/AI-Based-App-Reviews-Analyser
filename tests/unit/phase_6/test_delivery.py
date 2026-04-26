"""Tests for the Phase 6 delivery orchestrator (6a Docs + 6b Gmail)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from pydantic import AnyUrl

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.types import ProductRegistryEntry, RunPlan
from pulse.phase_5.types import DocBlock, DocReport, EmailReport
from pulse.phase_6 import deliver
from pulse.phase_6.mcp_servers.docs_server import mcp as docs_mcp
from pulse.phase_6.mcp_servers.docs_server import reset as docs_reset
from pulse.phase_6.mcp_servers.gmail_server import mcp as gmail_mcp
from pulse.phase_6.mcp_servers.gmail_server import reset as gmail_reset


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_stores():
    docs_reset()
    gmail_reset()
    yield
    docs_reset()
    gmail_reset()


def _make_plan(
    *,
    dry_run: bool = False,
    draft_only: bool = False,
    doc_id: str = "doc-test-1",
    slug: str = "groww",
    iso_week: str = "2026-W17",
) -> RunPlan:
    product = ProductRegistryEntry(
        slug=slug,
        display_name="Groww",
        pulse_doc_id=doc_id,
        email_recipients=["team@example.com"],
        app_store_id="1404379703",
        play_store_id="com.nextbillion.groww",
    )
    return RunPlan(
        run_id=uuid.uuid4(),
        product=product,
        iso_week=iso_week,
        window_start=datetime(2026, 4, 20, tzinfo=timezone.utc),
        window_end=datetime(2026, 4, 26, tzinfo=timezone.utc),
        sources=["app_store", "play_store"],
        llm_model="llama-3.3-70b-versatile",
        embedding_model="text-embedding-3-small",
        mcp_docs_url=AnyUrl("http://localhost:8080/sse"),
        mcp_gmail_url=AnyUrl("http://localhost:8081/sse"),
        dry_run=dry_run,
        draft_only=draft_only,
    )


_ANCHOR = "pulse-groww-2026-W17"

_BLOCKS = [
    DocBlock(type="heading_2", text="Week 2026-W17", anchor=_ANCHOR),
    DocBlock(type="paragraph", text="Summary paragraph."),
]

_DOC_REPORT = DocReport(
    anchor=_ANCHOR,
    blocks=_BLOCKS,
    metadata={"run_id": "test"},
)

_EMAIL_REPORT = EmailReport(
    subject="Groww Pulse — 2026-W17",
    html_body="<p>See full report at {{PULSE_DEEP_LINK}}</p>",
    text_body="See full report at {{PULSE_DEEP_LINK}}",
)


def _in_process_client_patch(docs_mcp_instance, gmail_mcp_instance):
    """Patch fastmcp.Client so URLs resolve to in-process MCP instances."""
    from fastmcp import Client as RealClient

    original_init = RealClient.__init__

    def _patched_init(self, target, *args, **kwargs):
        target_str = str(target)
        if "8080" in target_str:
            original_init(self, docs_mcp_instance, *args, **kwargs)
        elif "8081" in target_str:
            original_init(self, gmail_mcp_instance, *args, **kwargs)
        else:
            original_init(self, target, *args, **kwargs)

    return patch.object(RealClient, "__init__", _patched_init)


# ── Dry-run tests ─────────────────────────────────────────────────────────────


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_returns_dry_run_statuses(self):
        plan = _make_plan(dry_run=True)
        receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert receipt.doc_status == "dry_run"
        assert receipt.email_status == "dry_run"
        assert receipt.dry_run is True
        assert receipt.doc_revision_id == "dry_run_rev"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_write_to_docs(self):
        plan = _make_plan(dry_run=True)
        await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        # Nothing should have been written to the in-process docs store
        from pulse.phase_6.mcp_servers.docs_server import _store
        assert _store == {}

    @pytest.mark.asyncio
    async def test_dry_run_does_not_send_email(self):
        plan = _make_plan(dry_run=True)
        await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        from pulse.phase_6.mcp_servers.gmail_server import _sent, _drafts
        assert _sent == []
        assert _drafts == []


# ── Sub-phase 6a (Docs) tests ─────────────────────────────────────────────────


class TestDocsAppend:
    @pytest.mark.asyncio
    async def test_append_returns_appended_status(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert receipt.doc_status == "appended"
        assert receipt.doc_revision_id.startswith("rev_")

    @pytest.mark.asyncio
    async def test_append_twice_skips_second(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            r1 = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
            r2 = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert r1.doc_status == "appended"
        assert r2.doc_status == "skipped_existing_anchor"
        assert r2.doc_revision_id == r1.doc_revision_id

    @pytest.mark.asyncio
    async def test_doc_failure_raises_phase_failure(self):
        plan = _make_plan()
        bad_report = DocReport(anchor=_ANCHOR, blocks=[], metadata={})
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            with pytest.raises(PhaseFailure) as exc_info:
                await deliver(plan, bad_report, _EMAIL_REPORT)
        assert exc_info.value.phase == 6

    @pytest.mark.asyncio
    async def test_doc_failure_prevents_email_send(self):
        plan = _make_plan()
        bad_report = DocReport(anchor=_ANCHOR, blocks=[], metadata={})
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            with pytest.raises(PhaseFailure):
                await deliver(plan, bad_report, _EMAIL_REPORT)
        from pulse.phase_6.mcp_servers.gmail_server import _sent, _drafts
        assert _sent == []
        assert _drafts == []


# ── Sub-phase 6b (Gmail) tests ────────────────────────────────────────────────


class TestGmailSend:
    @pytest.mark.asyncio
    async def test_send_returns_sent_status(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert receipt.email_status == "sent"
        assert receipt.gmail_message_id is not None
        assert receipt.gmail_draft_id is None

    @pytest.mark.asyncio
    async def test_send_idempotency_skips_on_duplicate(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            r1 = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
            r2 = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert r1.email_status == "sent"
        assert r2.email_status == "skipped_already_sent"
        assert r2.gmail_message_id == r1.gmail_message_id

    @pytest.mark.asyncio
    async def test_draft_only_creates_draft(self):
        plan = _make_plan(draft_only=True)
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert receipt.email_status == "drafted"
        assert receipt.gmail_draft_id is not None
        assert receipt.gmail_message_id is None

    @pytest.mark.asyncio
    async def test_email_failure_returns_partial_receipt(self):
        """6b failure does not raise — returns partial receipt with email_status=failed."""
        plan = _make_plan()

        async def _bad_send(*_args, **_kwargs):
            raise RuntimeError("SMTP error")

        # Patch the name as imported in the orchestrator, not in gmail_adapter
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            with patch("pulse.phase_6.delivery.orchestrator.gmail_messages_send", _bad_send):
                receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)

        # Docs succeeded
        assert receipt.doc_status == "appended"
        # Email failed but did not raise
        assert receipt.email_status == "failed"
        assert receipt.gmail_message_id is None

    @pytest.mark.asyncio
    async def test_deep_link_injected_into_email_body(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)

        from pulse.phase_6.mcp_servers.gmail_server import _sent
        assert len(_sent) == 1
        sent_msg = _sent[0]
        assert "{{PULSE_DEEP_LINK}}" not in sent_msg["html_body"]
        assert "{{PULSE_DEEP_LINK}}" not in sent_msg["text_body"]
        assert "docs.google.com" in sent_msg["html_body"]


# ── Receipt shape tests ───────────────────────────────────────────────────────


class TestReceiptShape:
    @pytest.mark.asyncio
    async def test_receipt_fields_populated(self):
        plan = _make_plan()
        with _in_process_client_patch(docs_mcp, gmail_mcp):
            receipt = await deliver(plan, _DOC_REPORT, _EMAIL_REPORT)
        assert receipt.doc_id == "doc-test-1"
        assert receipt.doc_section_anchor == _ANCHOR
        assert receipt.sent_at is not None
        assert receipt.dry_run is False
