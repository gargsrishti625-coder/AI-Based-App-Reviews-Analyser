# Phase 6 — MCP Delivery (Docs + Gmail): Implementation

Deliver the report **only** through MCP servers. Docs append is idempotent on the section anchor; Gmail send/draft is idempotent on a key derived from `(product, iso_week, doc_revision_id)`.

**See also:** [architecture.md § Phase 6](../architecture.md), [evaluations/phase-6.md](../evaluations/phase-6.md), [edge-cases/phase-6.md](../edge-cases/phase-6.md).

---

## Goals

1. Sub-phase 6a: append the Doc section in a single `docs.batchUpdate`, skipping if the anchor already exists.
2. Sub-phase 6b: send (or draft, per `RunPlan.draft_only`) a teaser email with a deep link.
3. Build a `DeliveryReceipt` for Phase 7.
4. **No** Google API client imports anywhere; all Google access is via MCP.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/mcp/client.py` | Thin MCP host: connect, list tools, call tool; surfaces typed errors |
| `src/pulse/mcp/docs_adapter.py` | Typed wrappers: `docs_get`, `docs_batch_update` |
| `src/pulse/mcp/gmail_adapter.py` | Typed wrappers: `gmail_drafts_create`, `gmail_messages_send`, `gmail_messages_list` |
| `src/pulse/delivery/orchestrator.py` | Runs 6a → 6b, builds `DeliveryReceipt` |
| `src/pulse/core/types.py` | Add `DeliveryReceipt` |

---

## Data Models

```python
class DeliveryReceipt(BaseModel):
    doc_id: str
    doc_section_anchor: str
    doc_revision_id: str
    gmail_message_id: str | None
    gmail_draft_id: str | None
    sent_at: datetime
    dry_run: bool = False
```

---

## Sub-phase 6a — Docs Append

Steps:

1. **List tools** on the Docs MCP server. Verify `docs.get` and `docs.batchUpdate` are present (Phase 0 also does this; here it's a defense-in-depth check).
2. **Fetch the Doc** via `docs.get(doc_id=plan.product.pulse_doc_id)`.
3. **Anchor check**: scan the document structure for a heading element whose anchor / heading-id matches `report.anchor`. If found, capture the existing `revision_id` and skip the append.
4. **Build `batchUpdate` requests** by translating `DocBlock[]` into the Docs API request shape:
   - `insertText` requests appending at the end of the body.
   - `updateParagraphStyle` for headings (with `namedStyleType: "HEADING_2"` and a `headingId` set to `report.anchor`).
   - `createParagraphBullets` for lists.
   - `updateParagraphStyle` with `quoted: true` (or named blockquote style) for blockquote blocks.
5. **Single call**: send all requests in one `docs.batchUpdate(doc_id, requests=[...])` invocation. Capture the response's `documentRevisionId` → `doc_revision_id`.
6. **Verify**: re-fetch the Doc structure; assert the anchor now exists. If not, raise `PhaseFailure(6, "doc_append_unverified")` even though the call returned 200.
7. Build the deep link: `f"https://docs.google.com/document/d/{doc_id}/edit#heading={anchor}"`.

If `RunPlan.dry_run`: skip steps 4–6; populate `DeliveryReceipt` with `doc_id`, anchor, a synthetic revision id, `dry_run=True`.

---

## Sub-phase 6b — Gmail Send / Draft

Steps:

1. **Substitute** `{{PULSE_DEEP_LINK}}` in both HTML and text bodies with the deep link from 6a.
2. **Compute idempotency key**: `sha256(product.slug || iso_week || doc_revision_id).hexdigest()`.
3. **Pre-check**: call `gmail.messages.list(query=f"X-Pulse-Idempotency-Key:{key}", limit=1)` (or rfc822 header search). If a hit, capture its `message_id`, mark `email:skipped_already_sent`, and return.
4. **Headers**: `X-Pulse-Idempotency-Key: {key}`, `X-Pulse-Run-ID: {run_id}`.
5. **Send / draft**:
   - If `plan.dry_run`: skip; receipt has neither `gmail_message_id` nor `gmail_draft_id`, marker `dry_run=True`.
   - Elif `plan.draft_only`: call `gmail.drafts.create(...)`, capture `draft_id`.
   - Else: call `gmail.messages.send(...)`, capture `message_id`.

---

## MCP Client (`mcp/client.py`)

A thin wrapper:

```python
class McpSession:
    async def __aenter__(self) -> Self: ...
    async def list_tools(self) -> list[ToolDescriptor]: ...
    async def call(self, tool: str, **kwargs) -> dict: ...
```

Errors surface as `McpToolError(server, tool, code, message)` so the orchestrator can decide retry vs abort.

`tenacity` retries: one retry on transient errors (5xx-equivalent, network drop). Idempotency is preserved because:
- Docs `batchUpdate` retry re-runs the anchor check first; if the prior attempt actually succeeded, the second pass takes the skip branch.
- Gmail send retry consults the idempotency-key pre-check.

---

## Implementation Steps

1. **`mcp/client.py`** — implement against the `mcp` Python SDK. One `McpSession` per server; close on exit.
2. **`mcp/docs_adapter.py`**:
   - `async def docs_get(session, doc_id) -> DocStructure`.
   - `async def docs_batch_update(session, doc_id, requests) -> str` (returns `revision_id`).
   - Helper: `find_heading_with_anchor(doc, anchor) -> Optional[str]` returning the existing revision_id if found.
   - Helper: `blocks_to_requests(blocks) -> list[dict]`.
3. **`mcp/gmail_adapter.py`**:
   - `async def gmail_messages_list(session, query, limit) -> list[Message]`.
   - `async def gmail_messages_send(session, *, to, subject, html, text, headers) -> str`.
   - `async def gmail_drafts_create(session, ...) -> str`.
4. **`delivery/orchestrator.py`**:
   - `async def deliver(plan, doc_report, email_report) -> DeliveryReceipt`.
   - Calls 6a then 6b. Wraps each sub-phase in its own try/except so a 6b failure produces a `partial` outcome rather than re-running 6a.
5. **`PhaseFailure` mapping**: 6a failure → `failed_phase=6`, `error="docs:..."`. 6b failure → `failed_phase=6`, `error="gmail:..."`, but the receipt still includes `doc_*` fields so the audit row is informative.

---

## Tests to Add

Mapped to [evaluations/phase-6.md](../evaluations/phase-6.md):

- `test_docs_append_new_section` (P6a-E1) — fake MCP server records the `batchUpdate` call.
- `test_docs_skip_when_anchor_exists` (P6a-E2).
- `test_phase_6_fails_when_batchupdate_missing` (P6a-E3).
- `test_docs_batchupdate_retried_once_then_succeeds` (P6a-E4).
- `test_deep_link_construction` (P6a-E5).
- `test_dry_run_no_mcp_writes` (P6a-E6).
- `test_draft_only_creates_draft` (P6b-E1).
- `test_send_when_not_draft_only` (P6b-E2).
- `test_idempotency_header_present` (P6b-E3).
- `test_email_skipped_when_prior_send_detected` (P6b-E4).
- `test_partial_outcome_doc_ok_email_failed` (P6b-E5).
- `test_deep_link_placeholder_replaced` (P6b-E6).

Edge cases from [edge-cases/phase-6.md](../edge-cases/phase-6.md):

- MCP tool renamed; tool list empty; doc not found; permission denied; anchor exists with different content; Gmail rate limit; header dropped by MCP server; revision_id null.

CI invariant test: `tests/test_no_google_sdk.py` (already added in Phase 0) catches accidental imports.

---

## Dependencies

- New libs: `mcp` (host SDK).

---

## Definition of Done

- Against a real MCP pair, an end-to-end smoke run on a staging product produces:
  - A new dated H2 section in the product Doc.
  - A Gmail draft (because `--draft-only` is the staging default).
- Re-running the same week appends nothing and creates no second draft.
- All evaluations P6a/P6b pass.
- The CI grep confirms zero direct Google SDK usage in `src/pulse/`.
