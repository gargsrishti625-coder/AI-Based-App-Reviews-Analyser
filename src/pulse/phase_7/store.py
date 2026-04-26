"""SQLite DAO for audit records.

Schema version: 1
All datetimes stored as ISO-8601 UTC strings.
Uses BEGIN IMMEDIATE for writes to avoid deferred lock contention.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from pulse.phase_7.types import AuditRecord

log = structlog.get_logger()

_SCHEMA_VERSION = 1
_MAX_ERROR_BYTES = 8192

_DDL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    product             TEXT NOT NULL,
    iso_week            TEXT NOT NULL,
    started_at          TEXT NOT NULL,
    ended_at            TEXT,
    status              TEXT NOT NULL CHECK (status IN ('ok','partial','failed','skipped')),
    window_start        TEXT,
    window_end          TEXT,
    corpus_stats_json   TEXT,
    cluster_count       INTEGER,
    theme_count         INTEGER,
    llm_model           TEXT,
    total_tokens        INTEGER,
    total_cost_usd      REAL,
    doc_id              TEXT,
    doc_section_anchor  TEXT,
    doc_revision_id     TEXT,
    gmail_message_id    TEXT,
    gmail_draft_id      TEXT,
    failed_phase        INTEGER,
    error               TEXT,
    forced              INTEGER NOT NULL DEFAULT 0,
    dry_run             INTEGER NOT NULL DEFAULT 0,
    schema_version      INTEGER NOT NULL DEFAULT 1
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_real_send
    ON runs(product, iso_week)
    WHERE gmail_message_id IS NOT NULL AND forced = 0 AND dry_run = 0;

CREATE INDEX IF NOT EXISTS idx_runs_product_week ON runs(product, iso_week);
CREATE INDEX IF NOT EXISTS idx_runs_started      ON runs(started_at);
"""


def _dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _truncate_error(msg: str | None) -> str | None:
    if msg is None:
        return None
    encoded = msg.encode("utf-8")
    if len(encoded) > _MAX_ERROR_BYTES:
        return encoded[:_MAX_ERROR_BYTES].decode("utf-8", errors="replace") + "…[truncated]"
    return msg


class AuditStore:
    def __init__(self, db_path: Path) -> None:
        self._path = db_path

    # ── Schema ────────────────────────────────────────────────────────────────

    def migrate(self) -> None:
        """Create schema on first run; raise if schema version mismatches."""
        con = self._connect()
        try:
            with con:
                con.executescript(_DDL)
                row = con.execute(
                    "SELECT value FROM schema_meta WHERE key = 'version'"
                ).fetchone()
                if row is None:
                    con.execute(
                        "INSERT INTO schema_meta(key, value) VALUES ('version', ?)",
                        (str(_SCHEMA_VERSION),),
                    )
                else:
                    on_disk = int(row[0])
                    if on_disk != _SCHEMA_VERSION:
                        raise RuntimeError(
                            f"Audit DB schema version mismatch: "
                            f"code={_SCHEMA_VERSION}, on-disk={on_disk}. "
                            "Manual migration required."
                        )
        finally:
            con.close()

    # ── Writes ────────────────────────────────────────────────────────────────

    def insert(self, record: AuditRecord) -> None:
        """Insert a new audit row inside a BEGIN IMMEDIATE transaction."""
        from pulse.phase_7.types import AuditRecord as _AR  # noqa: F401

        corpus_json: str | None = None
        if record.corpus_stats is not None:
            corpus_json = record.corpus_stats.model_dump_json()

        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """
                INSERT INTO runs (
                    run_id, product, iso_week, started_at, ended_at, status,
                    window_start, window_end, corpus_stats_json,
                    cluster_count, theme_count, llm_model,
                    total_tokens, total_cost_usd,
                    doc_id, doc_section_anchor, doc_revision_id,
                    gmail_message_id, gmail_draft_id,
                    failed_phase, error, forced, dry_run, schema_version
                ) VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
                """,
                (
                    str(record.run_id),
                    record.product,
                    record.iso_week,
                    _dt(record.started_at),
                    _dt(record.ended_at),
                    record.status,
                    _dt(record.window_start),
                    _dt(record.window_end),
                    corpus_json,
                    record.cluster_count,
                    record.theme_count,
                    record.llm_model,
                    record.total_tokens,
                    record.total_cost_usd,
                    record.doc_id,
                    record.doc_section_anchor,
                    record.doc_revision_id,
                    record.gmail_message_id,
                    record.gmail_draft_id,
                    record.failed_phase,
                    _truncate_error(record.error),
                    int(record.forced),
                    int(record.dry_run),
                    _SCHEMA_VERSION,
                ),
            )
            con.execute("COMMIT")
            log.info("audit_insert", run_id=str(record.run_id), status=record.status)
        except sqlite3.IntegrityError as exc:
            con.execute("ROLLBACK")
            raise
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def update_terminal(self, run_id: UUID, **fields) -> None:
        """Update mutable fields on an existing row (called at run end)."""
        if not fields:
            return

        allowed = {
            "ended_at", "status", "corpus_stats_json", "cluster_count",
            "theme_count", "llm_model", "total_tokens", "total_cost_usd",
            "doc_id", "doc_section_anchor", "doc_revision_id",
            "gmail_message_id", "gmail_draft_id", "failed_phase", "error",
        }
        # Coerce datetimes and errors
        if "ended_at" in fields:
            fields["ended_at"] = _dt(fields["ended_at"])
        if "error" in fields:
            fields["error"] = _truncate_error(fields["error"])
        if "corpus_stats" in fields:
            cs = fields.pop("corpus_stats")
            fields["corpus_stats_json"] = cs.model_dump_json() if cs is not None else None

        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown audit fields: {unknown}")

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [str(run_id)]

        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                f"UPDATE runs SET {set_clause} WHERE run_id = ?", values
            )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get(self, run_id: UUID) -> AuditRecord | None:
        con = self._connect()
        try:
            row = con.execute(
                "SELECT * FROM runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
            return _row_to_record(row) if row else None
        finally:
            con.close()

    def list(self, product: str | None = None, limit: int = 50) -> list[AuditRecord]:
        con = self._connect()
        try:
            if product:
                rows = con.execute(
                    "SELECT * FROM runs WHERE product = ? ORDER BY started_at DESC LIMIT ?",
                    (product, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_record(r) for r in rows]
        finally:
            con.close()

    def find_prior_send(self, product: str, iso_week: str) -> AuditRecord | None:
        """Return the most recent non-forced, non-dry-run completed send for (product, iso_week)."""
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT * FROM runs
                WHERE product = ? AND iso_week = ?
                  AND gmail_message_id IS NOT NULL
                  AND forced = 0 AND dry_run = 0
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (product, iso_week),
            ).fetchone()
            return _row_to_record(row) if row else None
        finally:
            con.close()

    def find_partial(self, product: str, iso_week: str) -> AuditRecord | None:
        """Return the most recent partial run (doc ok, email failed) for (product, iso_week)."""
        con = self._connect()
        try:
            row = con.execute(
                """
                SELECT * FROM runs
                WHERE product = ? AND iso_week = ?
                  AND status = 'partial'
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (product, iso_week),
            ).fetchone()
            return _row_to_record(row) if row else None
        finally:
            con.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(self._path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con


def _row_to_record(row: sqlite3.Row) -> AuditRecord:
    from pulse.phase_7.types import AuditRecord

    corpus_stats = None
    if row["corpus_stats_json"]:
        from pulse.phase_2.core.types import CorpusStats
        corpus_stats = CorpusStats.model_validate_json(row["corpus_stats_json"])

    return AuditRecord(
        run_id=UUID(row["run_id"]),
        product=row["product"],
        iso_week=row["iso_week"],
        started_at=_parse_dt(row["started_at"]),  # type: ignore[arg-type]
        ended_at=_parse_dt(row["ended_at"]),
        status=row["status"],
        window_start=_parse_dt(row["window_start"]),
        window_end=_parse_dt(row["window_end"]),
        corpus_stats=corpus_stats,
        cluster_count=row["cluster_count"],
        theme_count=row["theme_count"],
        llm_model=row["llm_model"],
        total_tokens=row["total_tokens"],
        total_cost_usd=row["total_cost_usd"],
        doc_id=row["doc_id"],
        doc_section_anchor=row["doc_section_anchor"],
        doc_revision_id=row["doc_revision_id"],
        gmail_message_id=row["gmail_message_id"],
        gmail_draft_id=row["gmail_draft_id"],
        failed_phase=row["failed_phase"],
        error=row["error"],
        forced=bool(row["forced"]),
        dry_run=bool(row["dry_run"]),
    )
