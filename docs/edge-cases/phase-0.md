# Phase 0 — Bootstrap & Run Plan: Edge Cases

Failure modes and boundary conditions for the Bootstrap phase.

## Configuration

- **Missing config file** → fail fast with a clear path-not-found error; no partial `RunPlan`.
- **Malformed YAML/TOML** → schema validation error names the offending key/line; do not fall back to defaults silently.
- **Unknown product on CLI** (`--product mystery`) → reject before any MCP probe; list known products in the error.
- **Empty product registry** → reject; nothing to run.
- **Missing required field** (e.g. `mcp.docs_endpoint`) → reject with field name; do not invent localhost defaults.
- **Conflicting flags**: `--dry-run` + `--draft-only` → both honored (dry-run wins, no MCP calls at all).

## ISO Week Inputs

- **Future ISO week** (`--week 2027-W01` while today is in 2026) → reject; window must be fully in the past.
- **ISO week 53** (only valid in long years like 2026) → must parse correctly; reject for years that have no W53.
- **Malformed week string** (`2026-17`, `26-W17`, `2026W17`) → reject with example of valid format.
- **DST / timezone edge**: scheduled "now" near Monday 00:00 IST — ensure ISO week is computed in IST, not UTC, to avoid running last week's pulse.
- **Week boundary at year change**: `2025-W53` may map to dates spanning two calendar years — verify `window_start/end` aren't capped at year boundary.

## MCP Probes

- **Docs MCP unreachable** (TCP refused / DNS fail) → abort Phase 0 with `mcp:docs_unreachable`; do not proceed to Phase 1.
- **Gmail MCP unreachable** but `--draft-only` set → still abort; we won't be able to deliver later.
- **MCP probe times out** (slow but eventually responds) → respect `probe_timeout_ms`; do not hang the run.
- **MCP server up but missing required tool** (e.g. exposes `docs.get` but not `docs.batchUpdate`) → reject with the tool name that's missing; this catches MCP server downgrades.
- **MCP server returns extra tools** → ignored, not an error.
- **MCP server responds with unexpected schema** → log raw response; reject.

## Secrets

- **`GOOGLE_OAUTH_TOKEN` accidentally present in agent env** → log a warning (architecture invariant: tokens belong to MCP servers only). Do not refuse to run, but record in audit.
- **LLM key missing** → reject at Phase 0, not at Phase 4 (fail fast).
- **Ingestion key missing for a configured source** → drop that source from `RunPlan.sources` with a recorded warning, rather than aborting.

## Run Identity

- **Two concurrent invocations for the same `(product, iso_week)`** → both generate distinct `run_id`s. Phase 7's idempotency contract is the gate, not Phase 0. (See `edge-cases/phase-7.md`.)
- **Clock skew** between scheduler and host → `run_id` is uuid4 (not time-based), so unaffected.

## Frozen Plan Invariant

- Any module attempting to set a field on `RunPlan` post-freeze must raise — guard against accidental mutation in Phases 1–8.
- Serializing `RunPlan` to JSON for audit must round-trip exactly (no floats, no datetime drift).
