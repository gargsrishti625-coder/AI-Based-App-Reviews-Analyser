# Phase 8 — Scheduling & CLI: Edge Cases

Failure modes and boundary conditions for scheduling and the CLI.

## ISO Week / Timezone

- **Cron fires Monday 00:30 IST** but server is UTC (Sunday 19:00 UTC) — last completed ISO week must be computed in IST. A naive UTC computation could resolve to the wrong week at week boundaries.
- **DST transition** in target locales — IST has no DST, but if the cron runs from a host in a DST locale, ensure the cron expression is interpreted in IST (or use UTC and adjust).
- **Year boundary**: Monday 2026-01-05 06:00 IST resolves to ISO week `2026-W01`, not the prior year's W53 — depends on calendar; verify with both common cases.
- **`--week` in the future** → reject; window must be fully in the past.
- **`--week` for current incomplete week** → reject; the pulse needs full-week data.
- **`--week` predates the registry** (product was added later) → run normally; it will likely produce a small corpus or skip.

## Backfill

- **Backfill range malformed** (`--weeks 2026-W17..2026-W10`, descending) → reject, suggest correct order.
- **Backfill range crosses year boundary** (`2025-W52..2026-W02`) → iterate correctly: W52, W53 (if exists), W01, W02.
- **One backfill week fails** mid-range → continue with remaining weeks (each is independent); aggregate exit code reflects the failure.
- **Backfill week already has audit `status=ok`** → skip, do not re-fetch reviews unless `--force-resend` is set, which only forces email and not the Doc append.
- **Backfill of a very old week** (8+ weeks ago) → ingestion sources may have aged out the data; corpus may be empty → clean `status=skipped`.

## Scheduled Cadence

- **Cron retry on transient failure** (e.g. host restarted) → second run hits Phase 7 idempotency and exits as `skipped` if the first run completed; if the first run was mid-flight, see "Concurrent Runs" in `edge-cases/phase-7.md`.
- **Scheduler runs but `pulse run` binary missing / venv broken** → fail loud (exit 127); operator investigates. Do not produce an audit row in this case; the agent never started.
- **Product added to registry mid-week** → it gets included starting next Monday; no special handling.
- **Product removed from registry** → no future runs for it; existing audit rows untouched.
- **Registry change between scheduler-tick computation and run execution** → snapshot the registry at tick time; do not re-read mid-iteration.

## CLI Behavior

- **`pulse run` without `--product`** → reject; suggest registry contents.
- **`pulse run --product unknown`** → reject; list known products.
- **`pulse audit show <run_id>` for unknown id** → exit non-zero with "no such run".
- **Both `--dry-run` and `--draft-only`** → dry-run dominates (no MCP write of any kind).
- **`PULSE_ENV=prod` env var with `--draft-only` overridden to `false` on CLI** → CLI wins; log a warning that prod default was bypassed.
- **`--force-resend` without `--week`** → reject as ambiguous; force which week's email?

## Multi-Product Iteration

- **One product's run wedges** (e.g. MCP server hangs) → enforce a per-run wall-clock timeout (e.g. 10 min) so the next product still gets its turn.
- **Logs interleave** when products run sequentially — fine; each line is keyed by `product` and `run_id`.
- **Product runs share rate-limited resources** (LLM, embedding API) → sequential execution naturally serializes; document that concurrency would require coordinated rate-limit budgets.

## Exit Code Semantics

- **Aggregate exit code policy** for backfill / scheduled iteration: 0 if all `ok`/`skipped`; non-zero if any `failed`/`partial`. Document so operators can wire alerts.
- **`partial` outcome** (Doc ok, email failed) → non-zero exit by default; configurable per environment if email failures shouldn't page on weekends.

## Operational

- **Scheduler stops firing** (cron disabled, Action workflow paused) → no automatic detection; out of scope for v1. Mention in runbook.
- **Audit DB on a different volume than the agent** → fine; ensure the path is configurable.
- **Multiple agent installs on the same audit DB** → undefined; document "one writer per DB" as the v1 constraint.
