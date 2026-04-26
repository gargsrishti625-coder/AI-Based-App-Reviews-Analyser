# Phase 6 — MCP Delivery (Docs + Gmail): Evaluations

Evaluation criteria, test cases, and acceptance gates for delivery via Docs MCP and Gmail MCP.

## Quality Criteria

- **All Google access goes through MCP servers.** No direct Google REST/SDK calls anywhere in the agent codebase.
- A single `docs.batchUpdate` per week appends the section atomically — partial sections never appear.
- Idempotency is honored: re-running for the same `(product, iso_week)` does not produce duplicate Doc sections.
- Gmail send vs draft is determined solely by `RunPlan.draft_only`.

## Functional Tests — Sub-phase 6a (Docs MCP)

| ID | Scenario | Expected |
|---|---|---|
| P6a-E1 | Doc has no prior section for this week | `docs.batchUpdate` runs once; new section appended; `doc_revision_id` captured |
| P6a-E2 | Doc already has a heading with the section anchor | Append skipped; existing anchor reused; `doc_revision_id` recorded for the existing rev |
| P6a-E3 | `docs.list_tools` does not expose `batchUpdate` | Phase 6a fails before any write; audit `failed_phase=6`, `error="docs.batchUpdate_missing"` |
| P6a-E4 | `batchUpdate` returns transient error | Retry once with same payload (the call is idempotent on our anchor check); on second failure, abort 6a |
| P6a-E5 | Deep link constructed | `https://docs.google.com/document/d/{doc_id}/edit#heading={anchor}` |
| P6a-E6 | `--dry-run` flag | No `batchUpdate` issued; `DeliveryReceipt` populated with synthetic ids and a `dry_run=true` marker |

## Functional Tests — Sub-phase 6b (Gmail MCP)

| ID | Scenario | Expected |
|---|---|---|
| P6b-E1 | `RunPlan.draft_only=true` | `gmail.drafts.create` called; `gmail_draft_id` captured |
| P6b-E2 | `RunPlan.draft_only=false` | `gmail.messages.send` called; `gmail_message_id` captured |
| P6b-E3 | Email contains `X-Pulse-Idempotency-Key: sha256(product || iso_week || doc_revision_id)` | Header present and matches expected hash |
| P6b-E4 | Re-run after a successful prior send (no `--force-resend`) | Phase 7's check refuses; Phase 6b is skipped; audit notes "email already sent" |
| P6b-E5 | Phase 6a succeeds, 6b fails | Audit records `doc:ok, email:failed`; run status `partial`; no Doc rollback |
| P6b-E6 | Deep-link placeholder replaced before send | The sent body contains the resolved URL; no `{{PULSE_DEEP_LINK}}` remains |

## Non-Goals (verify these are NOT in the agent)

- No `google-api-python-client` or `googleapiclient` imports.
- No `client_id`/`client_secret`/`refresh_token` in agent config or env.
- No direct call to `https://docs.googleapis.com` or `https://gmail.googleapis.com`.

## Determinism / Idempotency

- Anchor check before `batchUpdate` is the only write-side guard for the Doc.
- Email idempotency key is deterministic: `sha256(product || iso_week || doc_revision_id)` — re-runs after a Doc append produce the same key.

## Metrics to Log

- `mcp_call_count` per server, `mcp_call_duration_ms` per call
- `docs_batchupdate_status` (`ok | skipped_existing_anchor | failed`)
- `gmail_action` (`sent | drafted | skipped_already_sent | failed`)
- `doc_revision_id`, `gmail_message_id | gmail_draft_id`

## Acceptance Gate

The phase passes when:
1. `DeliveryReceipt` is fully populated, **and**
2. Doc has the dated section (or had it from a prior run), **and**
3. Email send/draft completed (or was correctly skipped on re-run).

A `partial` outcome (doc:ok, email:failed) is acceptable for Phase 6 — Phase 7 will record it and the operator re-runs to retry only the email.
