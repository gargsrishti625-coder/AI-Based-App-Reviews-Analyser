# Phase 8 — Scheduling & CLI: Evaluations

Evaluation criteria, test cases, and acceptance gates for scheduling and the CLI.

## Quality Criteria

- The pipeline is invocable both on a weekly cadence (cron / Action / Cloud Scheduler) and from a developer's terminal for backfills.
- Every CLI invocation produces an `AuditRecord` (Phase 7), even when the run is a no-op.
- The default cadence is **Monday morning IST** — week computation is timezone-aware.
- CI/staging environments default to `--draft-only` so a misconfigured deploy cannot email stakeholders.

## CLI Surface

| Command | Purpose | Exit code semantics |
|---|---|---|
| `pulse run --product <p> [--week 2026-W17] [--draft-only] [--dry-run]` | Single-week run | 0 on `ok`/`skipped`, non-zero on `failed`/`partial` (configurable) |
| `pulse backfill --product <p> --weeks 2026-W10..2026-W17` | Iterate weeks in order, fully independent | Aggregate exit code reflects worst run |
| `pulse audit show <run_id>` | Print human summary | 0 if found, non-zero if not |
| `pulse audit list --product <p>` | Reverse-chronological run list | 0 |
| `pulse mcp probe` | Connect to MCP servers and list tools | 0 if all servers respond with required tools |

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P8-E1 | Scheduled tick on Monday 06:00 IST | `iso_week` resolves to last completed ISO week (i.e., not the week that just started); one run per product in registry |
| P8-E2 | `pulse run --product groww` (no `--week`) | Same week resolution as scheduled tick |
| P8-E3 | `pulse backfill --weeks 2026-W10..2026-W12` | 3 runs in order, each with distinct `run_id`, each idempotent if rerun |
| P8-E4 | One product fails, other 4 succeed in scheduled iteration | The 4 succeed; the failure is recorded; exit code reflects partial failure |
| P8-E5 | CI environment with `PULSE_ENV=staging` | `--draft-only` defaulted to true unless explicitly overridden |
| P8-E6 | `pulse mcp probe` with missing tool | Non-zero exit; clear message naming the missing tool |
| P8-E7 | Backfill week already delivered | Run is `skipped`; backfill continues to next week |
| P8-E8 | `--week` matches current incomplete week | Reject (window must be fully past); suggest the prior week |

## Determinism / Reproducibility

- Backfill of `W10..W17` produces the same audit rows whether run as one invocation or eight separate invocations of `pulse run`.
- ISO week resolution is computed in IST consistently — no UTC drift.
- Product registry iteration order is stable (alphabetical by product slug) so logs are diffable.

## Cadence / Scheduler

- Recommended: cron `0 6 * * 1` in IST (Monday 06:00 IST).
- One run per product per tick, **sequentially**. Concurrency is unnecessary at v1 scale (~5 products) and would complicate audit semantics.
- The scheduler shim is thin: it iterates the product registry and invokes `pulse run` per product; it does not own business logic.

## Metrics to Log

- `scheduled_tick_at`, `products_in_registry`, `products_succeeded`, `products_skipped`, `products_failed`
- Per-product: linked to the run's `run_id` via Phase 7

## Acceptance Gate

A scheduled run passes when:
1. An `AuditRecord` with `status in {ok, skipped}` exists for each product in the registry.
2. The corresponding Doc has the dated section.
3. A stakeholder email exists (or a draft, if `--draft-only`).

A backfill passes when each week's run individually meets the gate.
