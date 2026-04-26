# Phase 7 — Idempotency, Audit, Observability: Implementation

Persist a per-run `AuditRecord` to SQLite and enforce the idempotency contract: re-running for the same `(product, iso_week)` is a no-op unless `--force-resend`.

**See also:** [architecture.md § Phase 7](../architecture.md), [evaluations/phase-7.md](../evaluations/phase-7.md), [edge-cases/phase-7.md](../edge-cases/phase-7.md).

---

## Goals

1. Exactly one `AuditRecord` per run with a valid `status`.
2. Idempotency contract enforced via two layers: Doc anchor (server-side) + audit-store unique check (client-side).
3. `pulse audit show` and `pulse audit list` are read-only inspectors.
4. Structured JSON logs for the entire run with consistent context keys.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/audit/store.py` | SQLite schema + DAO |
| `src/pulse/audit/idempotency.py` | Pre-run idempotency check; force-resend semantics |
| `src/pulse/obs/logger.py` | structlog setup |
| `src/pulse/cli/main.py` | `pulse audit show` / `pulse audit list` subcommands |

---

## SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  product TEXT NOT NULL,
  iso_week TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('ok','partial','failed','skipped')),
  window_start TEXT, window_end TEXT,
  corpus_stats_json TEXT,
  cluster_count INTEGER, theme_count INTEGER,
  llm_model TEXT, total_tokens INTEGER, total_cost_usd REAL,
  doc_id TEXT, doc_section_anchor TEXT, doc_revision_id TEXT,
  gmail_message_id TEXT, gmail_draft_id TEXT,
  failed_phase INTEGER, error TEXT,
  forced INTEGER NOT NULL DEFAULT 0,
  dry_run INTEGER NOT NULL DEFAULT 0,
  schema_version INTEGER NOT NULL DEFAULT 1
);

-- One non-forced "real send" per (product, iso_week)
CREATE UNIQUE INDEX IF NOT EXISTS idx_real_send
  ON runs(product, iso_week)
  WHERE gmail_message_id IS NOT NULL AND forced = 0 AND dry_run = 0;

-- For fast lookups
CREATE INDEX IF NOT EXISTS idx_runs_product_week ON runs(product, iso_week);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
```

The partial unique index is the central correctness guarantee for "no duplicate sends" while still allowing repeated `failed`/`skipped`/`forced`/`dry_run` rows.

---

## Data Models

```python
class AuditRecord(BaseModel):
    run_id: UUID
    product: str
    iso_week: str
    started_at: datetime
    ended_at: datetime | None = None
    status: Literal["ok","partial","failed","skipped"]
    window_start: datetime | None = None
    window_end: datetime | None = None
    corpus_stats: CorpusStats | None = None
    cluster_count: int | None = None
    theme_count: int | None = None
    llm_model: str | None = None
    total_tokens: int | None = None
    total_cost_usd: float | None = None
    doc_id: str | None = None
    doc_section_anchor: str | None = None
    doc_revision_id: str | None = None
    gmail_message_id: str | None = None
    gmail_draft_id: str | None = None
    failed_phase: int | None = None
    error: str | None = None
    forced: bool = False
    dry_run: bool = False
```

---

## DAO API (`audit/store.py`)

```python
class AuditStore:
    def __init__(self, db_path: Path): ...
    def migrate(self) -> None: ...                # creates / migrates schema; refuses on version mismatch
    def insert(self, record: AuditRecord) -> None:  # single-row write inside a transaction
    def update_terminal(self, run_id: UUID, **fields) -> None:
    def get(self, run_id: UUID) -> AuditRecord | None:
    def list(self, product: str | None = None, limit: int = 50) -> list[AuditRecord]:
    def find_prior_send(self, product: str, iso_week: str) -> AuditRecord | None:
```

All writes use `BEGIN IMMEDIATE` to avoid SQLite's default deferred locking; reads use a fresh connection per call to keep the API simple.

---

## Idempotency (`audit/idempotency.py`)

```python
def check_before_run(store, plan) -> Decision:
    """
    Returns one of:
      Decision.PROCEED              — no prior successful send
      Decision.SKIP_ALREADY_SENT    — a non-forced, non-dry_run send exists
      Decision.RETRY_EMAIL_ONLY     — a 'partial' run exists (doc:ok, email:failed)
      Decision.FORCE_RESEND         — operator passed --force-resend
    """
```

Decision flow at the start of a run:

1. If `plan.force_resend`: return `FORCE_RESEND`.
2. Look up `find_prior_send(product, iso_week)`.
3. If a prior `ok`-status row exists (with `gmail_*`): `SKIP_ALREADY_SENT`. Write a new audit row with `status=skipped` and a reference to the prior `run_id` in `error`.
4. If a prior `partial` row exists (doc anchor present, no email id): `RETRY_EMAIL_ONLY`. Phase 6a will detect the existing anchor and skip; Phase 6b runs.
5. Else: `PROCEED`.

For `FORCE_RESEND`: the email idempotency key in Phase 6b is salted with `run_id` so the Gmail header pre-check also yields no hit.

---

## Implementation Steps

1. **`obs/logger.py`** — structlog config:
   ```python
   structlog.configure(
       processors=[
           structlog.contextvars.merge_contextvars,
           structlog.processors.add_log_level,
           structlog.processors.TimeStamper(fmt="iso"),
           structlog.processors.JSONRenderer(),
       ],
   )
   ```
   Expose `bind_run_context(run_id, product, iso_week)` and `bind_phase(n)` helpers using `contextvars`.

2. **`audit/store.py`** — implement the DAO; include a `schema_version` table for migrations. `migrate()` raises if the on-disk version doesn't match the code's expected version (no auto-migration in v1).

3. **`audit/idempotency.py`** — implement `check_before_run()` and `effective_email_key(plan, doc_revision_id)`.

4. **Integrate into the run lifecycle** in `cli/main.py`:
   - At entry: insert a row with `status=ok` placeholder and `started_at` set; record the run as "in flight". Actually — better: defer the insert until completion to keep the schema simple. But then crashed runs have no audit row. **Decision**: insert at start with `status='failed'` and `error='in_flight'`; update at end. This ensures every started run has a row.
   - On `PhaseFailure`: `update_terminal(run_id, status='failed', failed_phase=N, error=str(e), ended_at=now())`.
   - On clean abort (e.g. both sources empty): `status='skipped'`.
   - On success: `status='ok'`, all delivery fields populated.
   - On 6a-ok / 6b-failed: `status='partial'`.

5. **`pulse audit show <run_id>`** — pretty-print the row as a human summary table.

6. **`pulse audit list --product <p>`** — last 50 rows, reverse chronological.

7. **Concurrent run guard**: when `Decision.PROCEED`, additionally check for a row with `started_at` within the last 30 minutes and `status='failed'` + `error='in_flight'`. If present, log a warning and proceed (it's likely a stale crashed run); operator can override.

---

## Tests to Add

Mapped to [evaluations/phase-7.md](../evaluations/phase-7.md):

- `test_successful_run_writes_ok_row` (P7-E1).
- `test_phase_4_abort_writes_failed_row` (P7-E2).
- `test_both_sources_empty_skipped_row` (P7-E3).
- `test_rerun_after_success_is_skipped` (P7-E4).
- `test_force_resend_creates_new_send` (P7-E5).
- `test_concurrent_runs_unique_constraint` (P7-E6) — two writers, one wins.
- `test_audit_show_renders_summary` (P7-E7).
- `test_audit_list_chronological` (P7-E8).

Edge cases from [edge-cases/phase-7.md](../edge-cases/phase-7.md):

- Schema version mismatch refuses to operate.
- Stale "in_flight" rows.
- DB corrupt → fail loudly.
- Audit row says "sent" but Gmail has no record (force-resend recourse).
- Doc anchor exists, audit missing → recovery row written.

---

## Dependencies

- Stdlib only (`sqlite3`, `uuid`). `structlog` for logs.

---

## Definition of Done

- Every `pulse run` ends with exactly one row in the audit DB.
- `pulse audit show <run_id>` works for any past run.
- The unique index prevents accidental duplicate sends in concurrent-run tests.
- Structured logs for a run can be filtered with `jq 'select(.run_id == "...")'` and contain phase markers throughout.
