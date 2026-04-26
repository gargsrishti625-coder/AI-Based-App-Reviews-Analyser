"""Tests for AuditStore — SQLite DAO."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pulse.phase_2.core.types import CorpusStats
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


@pytest.fixture
def store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "audit.db")
    s.migrate()
    return s


# ── migrate ───────────────────────────────────────────────────────────────────


class TestMigrate:
    def test_creates_db_on_first_run(self, tmp_path: Path):
        db = tmp_path / "audit.db"
        s = AuditStore(db)
        s.migrate()
        assert db.exists()

    def test_idempotent_second_migrate(self, store: AuditStore):
        store.migrate()  # should not raise

    def test_schema_version_mismatch_raises(self, tmp_path: Path):
        s = AuditStore(tmp_path / "audit.db")
        s.migrate()
        # Tamper the version
        con = sqlite3.connect(str(tmp_path / "audit.db"))
        con.execute("UPDATE schema_meta SET value = '99' WHERE key = 'version'")
        con.commit()
        con.close()

        s2 = AuditStore(tmp_path / "audit.db")
        with pytest.raises(RuntimeError, match="schema version mismatch"):
            s2.migrate()


# ── insert ────────────────────────────────────────────────────────────────────


class TestInsert:
    def test_insert_and_get_roundtrip(self, store: AuditStore):
        rec = _record()
        store.insert(rec)
        got = store.get(rec.run_id)
        assert got is not None
        assert got.run_id == rec.run_id
        assert got.product == "groww"
        assert got.iso_week == "2026-W17"
        assert got.status == "ok"

    def test_insert_with_corpus_stats(self, store: AuditStore):
        stats = CorpusStats(total_in=100, total_out=80, dropped_short=20)
        rec = _record(corpus_stats=stats)
        store.insert(rec)
        got = store.get(rec.run_id)
        assert got is not None
        assert got.corpus_stats is not None
        assert got.corpus_stats.total_in == 100
        assert got.corpus_stats.total_out == 80

    def test_insert_with_all_delivery_fields(self, store: AuditStore):
        rec = _record(
            doc_id="doc-123",
            doc_section_anchor="pulse-groww-2026-W17",
            doc_revision_id="rev_abc",
            gmail_message_id="msg_xyz",
            llm_model="llama-3.3-70b-versatile",
            total_tokens=5000,
            cluster_count=4,
            theme_count=3,
        )
        store.insert(rec)
        got = store.get(rec.run_id)
        assert got is not None
        assert got.gmail_message_id == "msg_xyz"
        assert got.total_tokens == 5000

    def test_duplicate_run_id_raises(self, store: AuditStore):
        rec = _record()
        store.insert(rec)
        with pytest.raises(sqlite3.IntegrityError):
            store.insert(rec)

    def test_unique_index_blocks_second_real_send(self, store: AuditStore):
        """Two non-forced sends for same (product, iso_week) → second raises."""
        rec1 = _record(gmail_message_id="msg_1")
        rec2 = _record(run_id=uuid.uuid4(), gmail_message_id="msg_2")
        store.insert(rec1)
        with pytest.raises(sqlite3.IntegrityError):
            store.insert(rec2)

    def test_unique_index_allows_forced_after_real_send(self, store: AuditStore):
        rec1 = _record(gmail_message_id="msg_1")
        rec2 = _record(run_id=uuid.uuid4(), gmail_message_id="msg_2", forced=True)
        store.insert(rec1)
        store.insert(rec2)  # forced=True bypasses the unique index

    def test_unique_index_allows_dry_run_after_real_send(self, store: AuditStore):
        rec1 = _record(gmail_message_id="msg_1")
        rec2 = _record(run_id=uuid.uuid4(), gmail_message_id="msg_2", dry_run=True)
        store.insert(rec1)
        store.insert(rec2)

    def test_failed_rows_not_blocked_by_unique_index(self, store: AuditStore):
        """Multiple failed rows for the same (product, iso_week) are allowed."""
        rec1 = _record(status="failed", gmail_message_id=None)
        rec2 = _record(run_id=uuid.uuid4(), status="failed", gmail_message_id=None)
        store.insert(rec1)
        store.insert(rec2)

    def test_error_field_truncated_at_8kb(self, store: AuditStore):
        long_error = "x" * 20_000
        rec = _record(status="failed", error=long_error)
        store.insert(rec)
        got = store.get(rec.run_id)
        assert got is not None
        assert len(got.error.encode("utf-8")) <= 8300  # 8192 + ellipsis


# ── update_terminal ───────────────────────────────────────────────────────────


class TestUpdateTerminal:
    def test_update_status_and_ended_at(self, store: AuditStore):
        rec = _record(status="failed", error="in_flight")
        store.insert(rec)
        ended = datetime(2026, 4, 26, 10, 5, 0, tzinfo=timezone.utc)
        store.update_terminal(rec.run_id, status="ok", ended_at=ended)
        got = store.get(rec.run_id)
        assert got is not None
        assert got.status == "ok"
        assert got.ended_at == ended

    def test_update_delivery_fields(self, store: AuditStore):
        rec = _record(status="failed", error="in_flight")
        store.insert(rec)
        store.update_terminal(
            rec.run_id,
            status="ok",
            doc_id="doc-abc",
            gmail_message_id="msg_123",
        )
        got = store.get(rec.run_id)
        assert got is not None
        assert got.gmail_message_id == "msg_123"

    def test_update_corpus_stats(self, store: AuditStore):
        rec = _record(status="failed", error="in_flight")
        store.insert(rec)
        stats = CorpusStats(total_in=50, total_out=40, dropped_short=10)
        store.update_terminal(rec.run_id, corpus_stats=stats, status="ok")
        got = store.get(rec.run_id)
        assert got is not None
        assert got.corpus_stats is not None
        assert got.corpus_stats.total_in == 50

    def test_update_unknown_field_raises(self, store: AuditStore):
        rec = _record()
        store.insert(rec)
        with pytest.raises(ValueError, match="Unknown audit fields"):
            store.update_terminal(rec.run_id, nonexistent_field="bad")


# ── list ──────────────────────────────────────────────────────────────────────


class TestList:
    def test_list_returns_all_rows(self, store: AuditStore):
        store.insert(_record(run_id=uuid.uuid4()))
        store.insert(_record(run_id=uuid.uuid4()))
        rows = store.list()
        assert len(rows) == 2

    def test_list_filtered_by_product(self, store: AuditStore):
        store.insert(_record(product="groww"))
        store.insert(_record(run_id=uuid.uuid4(), product="other"))
        rows = store.list(product="groww")
        assert len(rows) == 1
        assert rows[0].product == "groww"

    def test_list_respects_limit(self, store: AuditStore):
        for _ in range(5):
            store.insert(_record(run_id=uuid.uuid4()))
        rows = store.list(limit=3)
        assert len(rows) == 3

    def test_list_returns_reverse_chronological(self, store: AuditStore):
        r1 = _record(run_id=uuid.uuid4(), started_at=datetime(2026, 4, 20, tzinfo=timezone.utc))
        r2 = _record(run_id=uuid.uuid4(), started_at=datetime(2026, 4, 26, tzinfo=timezone.utc))
        store.insert(r1)
        store.insert(r2)
        rows = store.list()
        assert rows[0].run_id == r2.run_id  # newest first


# ── find_prior_send ───────────────────────────────────────────────────────────


class TestFindPriorSend:
    def test_finds_existing_send(self, store: AuditStore):
        rec = _record(gmail_message_id="msg_abc")
        store.insert(rec)
        found = store.find_prior_send("groww", "2026-W17")
        assert found is not None
        assert found.gmail_message_id == "msg_abc"

    def test_no_send_returns_none(self, store: AuditStore):
        store.insert(_record(gmail_message_id=None))
        assert store.find_prior_send("groww", "2026-W17") is None

    def test_forced_send_not_returned(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_forced", forced=True))
        assert store.find_prior_send("groww", "2026-W17") is None

    def test_dry_run_send_not_returned(self, store: AuditStore):
        store.insert(_record(gmail_message_id="msg_dry", dry_run=True))
        assert store.find_prior_send("groww", "2026-W17") is None

    def test_different_week_not_returned(self, store: AuditStore):
        store.insert(_record(iso_week="2026-W16", gmail_message_id="msg_old"))
        assert store.find_prior_send("groww", "2026-W17") is None


# ── find_partial ──────────────────────────────────────────────────────────────


class TestFindPartial:
    def test_finds_partial_run(self, store: AuditStore):
        rec = _record(status="partial", doc_revision_id="rev_abc")
        store.insert(rec)
        found = store.find_partial("groww", "2026-W17")
        assert found is not None
        assert found.status == "partial"

    def test_ok_run_not_returned(self, store: AuditStore):
        store.insert(_record(status="ok"))
        assert store.find_partial("groww", "2026-W17") is None

    def test_different_product_not_returned(self, store: AuditStore):
        store.insert(_record(product="other", status="partial"))
        assert store.find_partial("groww", "2026-W17") is None
