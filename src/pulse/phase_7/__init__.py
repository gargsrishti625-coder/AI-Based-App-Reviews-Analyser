"""Phase 7 — Idempotency, Audit, Observability.

Public API:
    AuditStore   — SQLite DAO for audit records
    AuditRecord  — Pydantic model for a single run row
    Decision     — enum returned by check_before_run()
    check_before_run(store, plan) -> Decision
"""
from pulse.phase_7.idempotency import Decision, check_before_run
from pulse.phase_7.store import AuditStore
from pulse.phase_7.types import AuditRecord

__all__ = ["AuditStore", "AuditRecord", "Decision", "check_before_run"]
