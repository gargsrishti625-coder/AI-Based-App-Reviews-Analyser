# Phase 7 — Idempotency, Audit, Observability: Evaluations

Evaluation criteria, test cases, and acceptance gates for the audit store and idempotency contract.

## Quality Criteria

- Re-running for `(product, iso_week)` is **safe** — no duplicate Doc sections, no duplicate emails (without `--force-resend`).
- Every run produces exactly one `AuditRecord` row, regardless of outcome (`ok`, `partial`, `failed`, `skipped`).
- The audit store can answer: "What was sent, when, for which week?" in a single query.
- Logs are structured JSON keyed on `run_id`, `product`, `iso_week`, `phase`.

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P7-E1 | Successful run | `AuditRecord` row with `status=ok`, all delivery ids populated, `failed_phase=null` |
| P7-E2 | Phase 4 abort (no themes survived) | Row with `status=failed`, `failed_phase=4`, `doc_id=null`, `gmail_*=null`, `error` non-null |
| P7-E3 | Both ingestion sources empty | Row with `status=skipped`, `failed_phase=null`, `corpus_stats` recorded |
| P7-E4 | Re-run for same `(product, iso_week)` after a successful prior run | `audit/idempotency.py` reports "already delivered"; Phase 6a skipped (anchor exists); Phase 6b skipped (audit-store says already sent); new audit row written with `status=skipped` and a reference to the prior `run_id` |
| P7-E5 | Re-run with `--force-resend` | Audit guard bypassed for email; new email sent; new audit row records this with a `forced=true` flag |
| P7-E6 | Two concurrent runs for same key | One wins the audit-store insert (transaction or unique constraint); the other fails fast with `status=skipped, reason=concurrent_run_detected` |
| P7-E7 | `pulse audit show <run_id>` | Renders a human summary including phase outcomes, delivery ids, token cost |
| P7-E8 | `pulse audit list --product <p>` | Lists rows in reverse chronological order with status emoji-free indicators |

## Schema

The SQLite table has at minimum:

```sql
CREATE TABLE runs (
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
  forced INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX idx_product_week_sent ON runs(product, iso_week)
  WHERE gmail_message_id IS NOT NULL;
```

The partial unique index enforces "one successful send per `(product, iso_week)`" without preventing multiple `failed`/`skipped` rows.

## Idempotency Contract

- **Doc-side**: section anchor `pulse-{product}-{iso_week}` is the dedup key; Phase 6a checks the Doc, not the audit store.
- **Email-side**: two checks compose:
  1. `X-Pulse-Idempotency-Key` header (visible to the MCP server / Gmail).
  2. Audit-store unique-on-`(product, iso_week, sent)` (visible to the agent).
- `--force-resend` bypasses (2) and rewrites the idempotency key to include a salt so the header check also lets it through.

## Determinism / Observability

- Every log line includes `{run_id, product, iso_week, phase}`.
- Cost reporting (`total_cost_usd`) is computed from token counts × model rate card stored in config.
- `pulse audit show` and `pulse audit list` are read-only and never mutate state.

## Metrics to Log

- `audit_write_duration_ms`
- `idempotency_decision` (`new | skip_anchor_exists | skip_already_sent | force_resend`)
- `db_lock_wait_ms` (if non-trivial)

## Acceptance Gate

The phase passes when:
1. Exactly one `AuditRecord` row exists for the run with a valid `status`.
2. The idempotency contract was honored (verified by re-running and observing no duplicate side effects).
3. `pulse audit show <run_id>` returns a non-empty summary.
