# Phase 7 — Idempotency, Audit, Observability: Edge Cases

Failure modes and boundary conditions for the audit store and idempotency contract.

## Concurrent Runs

- **Two schedulers fire for the same `(product, iso_week)`** (cron retried, two GitHub Action runners) → both attempt to insert the audit row guarded by the partial unique index. The loser gets a constraint violation and exits with `status=skipped, reason=concurrent_run_detected`.
- **Agent crashes mid-run, leaving an `started_at` row with no `ended_at`** → on re-run, treat rows older than a stale-threshold (e.g. 30 min) as crashed; allow the new run to proceed but link the prior `run_id` in `error`.
- **DB lock contention** on a busy host → SQLite is fine for single-host v1; if the host is multi-process, consider WAL mode and short transactions.

## SQLite Specifics

- **DB file missing** → create on first run with the canonical schema; do not silently create a wrong schema.
- **Schema drift** between code and DB → run a startup migration check; refuse to operate if the schema doesn't match the expected version.
- **Corrupt DB** (truncated, ENOSPC mid-write) → fail loudly; do not auto-rebuild (would lose audit history). Operator restores from backup.
- **WAL file growing unbounded** → checkpoint periodically; this is operational, not a correctness issue.
- **Datetime stored as text** — use ISO-8601 UTC strings; never store local time.

## Idempotency Edge Cases

- **Audit row says "sent" but Gmail has no record** (audit lied or Gmail purged) → `--force-resend` is the operator's recourse; do not auto-resend.
- **Gmail has a prior message but audit row missing** (audit DB lost) → `X-Pulse-Idempotency-Key` header check via Gmail MCP `list` is the second-layer defense; if it confirms a prior send, write a recovery audit row with `status=skipped, reason=detected_via_header`.
- **Doc has the anchor but audit says "no doc"** → trust the Doc; record `doc_id`, `doc_section_anchor`, `doc_revision_id` recovered from the existing section into the new audit row.
- **Conflict between Doc and audit** (Doc has a different revision than audit's `doc_revision_id`) → log; treat the Doc as truth; update audit. The email idempotency key for that week effectively rolls forward to the new revision.
- **`--force-resend` invoked when no prior send exists** → behave like a normal run; record `forced=true` for traceability but no special path.

## `--force-resend` Hazards

- **Operator force-resends repeatedly** → each forced run inserts a row with `forced=true`; the partial unique index `WHERE gmail_message_id IS NOT NULL` would block the second one. Either:
  - allow forced rows to bypass the unique index by setting `gmail_message_id` to a salted value, or
  - restructure the index to `WHERE gmail_message_id IS NOT NULL AND forced=0` so forced sends are not deduped.
  Document the chosen behavior; both are defensible.
- **Forced send but Doc anchor doesn't exist yet** (operator forces before any successful run) → run normally; force only affects email idempotency, not Doc anchor.

## Audit Record Hazards

- **Run completed Phase 6 but agent died before audit insert** → the side effects (Doc append, email) are visible in the world but not in audit. Recovery: a `pulse audit reconcile` command (out of v1 scope, but worth designing for) that scans Docs/Gmail for our markers and backfills audit rows. For v1: operator manually inserts.
- **`error` column contains a multi-line stack trace** → keep it; truncate to a max length (e.g. 8KB) so SQLite stays performant.
- **Cost / token columns null** for runs that aborted before Phase 4 → expected; reports must handle nulls.

## Observability

- **Log lines from MCP servers proxied through the agent** → tag with `mcp_server` to disambiguate; do not merge them into the agent's own log lines.
- **PII in logs** → never log `CleanReview.text` or `RawReview.body`; log only ids, hashes, and counts.
- **Run with `dry_run=true`** → still produces an audit row with `status=ok` and `dry_run=true` in a dedicated column or in `error` (depending on schema choice). Do not confuse a dry-run with a real send.

## Recovery

- **Phase 7 itself fails** (DB write fails) after Phase 6 succeeded → emit a CRITICAL log line with the full receipt so an operator can manually reconcile. The run exit code reflects the audit failure; the side effects in the Doc/Gmail still stand.
