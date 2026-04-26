# Phase 0 — Bootstrap & Run Plan: Evaluations

Evaluation criteria, test cases, and acceptance gates for the Bootstrap phase.

## Quality Criteria

- A `RunPlan` is fully populated, schema-valid, and **frozen** (immutable) before any I/O.
- Every field that downstream phases read has a deterministic source: CLI > config > defaults.
- No network call to ingestion or LLM is issued before the plan exists.
- MCP endpoints are reachable and expose the expected tool surface at probe time.

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P0-E1 | Valid CLI + config (`--product groww --week 2026-W17`) | `RunPlan` built; `iso_week == "2026-W17"`; `window_start/end` derived from window length |
| P0-E2 | `--week` omitted at scheduled run time | `iso_week` resolves to current ISO week in IST |
| P0-E3 | `--draft-only` flag set | `RunPlan.draft_only == true`; staging defaults still respected |
| P0-E4 | `--dry-run` flag | `RunPlan.dry_run == true`; Phase 6 will short-circuit |
| P0-E5 | Config has 3 products in registry, CLI selects one | Only that product's store IDs present in `RunPlan.sources` |
| P0-E6 | MCP probe succeeds (Docs + Gmail respond to `list_tools`) | Bootstrap exits successfully |
| P0-E7 | MCP probe response includes required tools (`docs.batchUpdate`, `gmail.messages.send` or `gmail.drafts.create`) | Pass |
| P0-E8 | `RunPlan` mutation attempted post-freeze | Raises / rejected (immutability invariant) |

## Determinism / Reproducibility

- Re-invoking with the same `(product, iso_week, config-hash)` produces an identical `RunPlan` except `run_id`.
- `window_start` and `window_end` are derived purely from `iso_week` and `window_length_weeks` — no clock drift.

## Metrics to Log

- `bootstrap_duration_ms`
- `mcp_probe_latency_ms` per server
- `config_hash` (for traceability across runs)
- `tools_listed_count` per MCP server

## Acceptance Gate

The phase passes when:
1. `RunPlan` validates against schema.
2. Both MCP servers respond to `list_tools` within timeout (default 5s).
3. The selected product is present in the registry.
4. ISO week is parseable and the resulting window is fully in the past (`window_end <= now`).
