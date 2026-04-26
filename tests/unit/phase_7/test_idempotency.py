"""Tests for the pre-run idempotency check."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pulse.phase_7.idempotency import Decision, check_before_run
from pulse.phase_7.store import AuditStore
from pulse.phase_7.types import AuditRecord

_NOW = datetime(2026, 4, 26, 10, 0, 0, tzinfo=timezone.utc)


def _record(**overrides) -> AuditRecord:
    defaults = dict(
        run_id=uuid.uuid4(),
        product="groww",
        iso_week="2026-W17",
        started_at=_NOW,
        status="ok",
    )
    defaults.update(overrides)
    return AuditRecord(**defaults)


def _plan(*, force_resend: bool = False) -> MagicMock:
    plan = MagicMock()
    plan.force_resend = force_resend
    plan.product.slug = "groww"
    plan.iso_week = "2026-W17"
    return plan


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "audit.db")
    s.migrate()
    return s


class TestCheckBeforeRun:
    def test_proceed_when_no_prior_record(self, store: AuditStore):
        decision = check_before_run(store, _plan())
        assert decision == Decision.PROCEED

    def test_skip_when_prior_send_exists(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_abc"))
        decision = check_before_run(store, _plan())
        assert decision == Decision.SKIP_ALREADY_SENT

    def test_retry_email_only_when_partial_exists(self, store: AuditStore):
        store.insert(_record(status="partial", doc_revision_id="rev_abc"))
        decision = check_before_run(store, _plan())
        assert decision == Decision.RETRY_EMAIL_ONLY

    def test_force_resend_overrides_skip(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_abc"))
        decision = check_before_run(store, _plan(force_resend=True))
        assert decision == Decision.FORCE_RESEND

    def test_force_resend_overrides_partial(self, store: AuditStore):
        store.insert(_record(status="partial"))
        decision = check_before_run(store, _plan(force_resend=True))
        assert decision == Decision.FORCE_RESEND

    def test_force_resend_with_no_history(self, store: AuditStore):
        """force_resend with no prior row still returns FORCE_RESEND."""
        decision = check_before_run(store, _plan(force_resend=True))
        assert decision == Decision.FORCE_RESEND

    def test_prior_send_takes_precedence_over_partial(self, store: AuditStore):
        """If both a send and a partial exist, send wins → SKIP_ALREADY_SENT."""
        store.insert(_record(status="partial", run_id=uuid.uuid4()))
        store.insert(_record(
            run_id=uuid.uuid4(),
            status="ok",
            gmail_message_id="msg_real",
        ))
        decision = check_before_run(store, _plan())
        assert decision == Decision.SKIP_ALREADY_SENT

    def test_failed_rows_do_not_block_new_run(self, store: AuditStore):
        store.insert(_record(status="failed", gmail_message_id=None))
        decision = check_before_run(store, _plan())
        assert decision == Decision.PROCEED

    def test_dry_run_send_does_not_block_real_run(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_dry", dry_run=True))
        decision = check_before_run(store, _plan())
        assert decision == Decision.PROCEED

    def test_forced_send_does_not_block_new_run(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_forced", forced=True))
        decision = check_before_run(store, _plan())
        assert decision == Decision.PROCEED

    def test_different_product_does_not_block(self, store: AuditStore):
        store.insert(_record(product="other_app", gmail_message_id="msg_other"))
        plan = _plan()
        plan.product.slug = "groww"
        decision = check_before_run(store, plan)
        assert decision == Decision.PROCEED

    def test_different_week_does_not_block(self, store: AuditStore):
        store.insert(_record(iso_week="2026-W16", gmail_message_id="msg_old"))
        decision = check_before_run(store, _plan())
        assert decision == Decision.PROCEED
