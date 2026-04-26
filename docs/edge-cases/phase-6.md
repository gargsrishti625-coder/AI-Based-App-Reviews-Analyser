# Phase 6 — MCP Delivery (Docs + Gmail): Edge Cases

Failure modes and boundary conditions for MCP-based delivery.

## MCP Connection / Tool Surface

- **MCP server reachable but tool list is empty** → fail before write; `failed_phase=6`, `error="docs.tools_unavailable"`.
- **Tool name renamed server-side** (`docs.batchUpdate` → `docs.batch_update`) → fail clearly with both the expected and observed names; do not auto-guess.
- **MCP server returns ambiguous tool definitions** (multiple `gmail.messages.send` variants) → reject; require exact match.
- **MCP transport drops mid-call** → retry once; on second failure abort. Critically, do not retry an already-applied `batchUpdate` blindly — re-check the anchor first.

## Docs MCP — Append Hazards

- **Doc not found** (wrong `doc_id` or product Doc deleted) → fail Phase 6a; suggest creating the Doc and recording its id in product registry.
- **Doc shared but not editable by the MCP-server's identity** → fail; surface the permission error verbatim.
- **Anchor already exists but content under it differs** from what we'd append → respect idempotency: skip the append. Do not "merge" or "replace" — that would silently change committed history.
- **Two anchors with the same name in the Doc** (manual edit by a human) → log a warning; treat first as canonical; do not append. Operator intervention required.
- **`batchUpdate` partial application** by the API (some requests applied, others not) → in practice the Docs API is transactional per `batchUpdate`, but defend by re-fetching the Doc after a non-2xx response and verifying the section is either fully present or fully absent before deciding to retry.
- **Doc grows past API limits** (very long Docs) — eventually the append may fail size limits. Out of v1 scope, but log and fail gracefully.

## Gmail MCP — Send Hazards

- **Recipient list misconfigured** (empty or invalid) → fail Phase 6b; do not silently send to no one or to a default.
- **Gmail rate limit / 429** → backoff, retry once; on persistent failure mark `email:failed`.
- **Gmail draft creation succeeds but `gmail_draft_id` not returned** → fail loudly; downstream re-run should use the idempotency-key header to detect the prior draft.
- **`X-Pulse-Idempotency-Key` header dropped by MCP server** → if the server strips it, the idempotency contract breaks. Probe for header preservation in `pulse mcp probe`; fail Phase 0 if not supported.
- **Two concurrent runs both pass the audit check** (race) and both send → mitigated by the audit-store check happening under a transaction in Phase 7. See `edge-cases/phase-7.md`.
- **Doc revision id changed between 6a and 6b** (someone edited the Doc) → idempotency key for the email reflects the revision *we* wrote; that's stable across re-runs of the same `(product, iso_week)`.

## Cross-sub-phase Coordination

- **6a succeeds, agent crashes before 6b** → re-run detects existing anchor, skips 6a's append, computes the same idempotency key, sends/drafts the email. Audit reflects the recovery.
- **6a returns OK but the Doc actually wasn't updated** (server bug) → next re-run won't see the anchor and will append again. Mitigation: after `batchUpdate`, re-fetch and verify the anchor exists before recording success.
- **6a appends, but `doc_revision_id` is null in the response** → the email idempotency key would be different across runs. Treat null as failure of 6a; audit accordingly.

## OAuth / Secret Hygiene

- **Stack trace from MCP server includes a token fragment** → scrub before logging; never persist MCP-server-side errors verbatim into the audit `error` column without redaction.
- **Agent env has `GOOGLE_*` variables** → architectural smell; warn loudly. The agent must not need them.

## Dry-run / Draft-only

- **`--dry-run` AND `--draft-only`** → dry-run wins; nothing sent or drafted; receipt has synthetic ids and `dry_run=true`.
- **`--draft-only` but Gmail MCP has no `gmail.drafts.create`** → fail Phase 0 probe; do not silently fall back to send.

## Recovery Paths

| Outcome of 6a | Outcome of 6b | Run status | What re-run will do |
|---|---|---|---|
| OK (new append) | OK | `ok` | Skip both (anchor present, audit records prior send) |
| OK (skipped, anchor existed) | OK | `ok` | Skip both |
| OK | failed | `partial` | Skip 6a, retry 6b |
| failed | not attempted | `failed` | Retry 6a, then 6b |
