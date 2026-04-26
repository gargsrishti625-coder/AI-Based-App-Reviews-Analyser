# Phase 8 — Scheduling & CLI: Implementation

Wrap the pipeline so it can run weekly on a schedule and from a developer's terminal for backfills. Make the CLI surface predictable, with consistent exit-code semantics for alerting.

**See also:** [architecture.md § Phase 8](../architecture.md), [evaluations/phase-8.md](../evaluations/phase-8.md), [edge-cases/phase-8.md](../edge-cases/phase-8.md).

---

## Goals

1. Single Typer-based CLI (`pulse`) with `run`, `backfill`, `audit show|list`, `mcp probe`.
2. Weekly scheduled cadence: Monday 06:00 IST, sequential per product.
3. Backfill iterates ISO weeks in order; each week is fully independent.
4. CI/staging defaults to `--draft-only` so a misconfigured deploy can't email stakeholders.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/cli/main.py` | Typer app: `run`, `backfill`, `audit`, `mcp` subcommands |
| `src/pulse/scheduler/weekly.py` | Entry shim: resolve "this week" in IST and iterate the registry |
| `.github/workflows/weekly.yaml` | Cron-driven GitHub Action |

---

## CLI Surface

```text
pulse run --product <p> [--week 2026-W17] [--draft-only/--no-draft-only]
                        [--dry-run] [--force-resend] [--config PATH]
pulse backfill --product <p> --weeks 2026-W10..2026-W17 [flags as above]
pulse audit show <run_id>
pulse audit list [--product <p>] [--limit 50]
pulse mcp probe [--config PATH]
```

### Defaults

- `--week`: when omitted, resolves to last completed ISO week in IST.
- `--draft-only`: defaults to `True` if `pulse_env in {"dev","staging"}`, else `False`. CLI flag overrides.
- `--dry-run`: defaults to `False`.
- `--force-resend`: defaults to `False`.
- `--config`: defaults to `./config/pulse.yaml`, overridable via `PULSE_CONFIG` env var.

### Exit-code semantics

| Run status | Exit code |
|---|---|
| `ok` | 0 |
| `skipped` | 0 |
| `partial` | 2 |
| `failed` | 1 |
| Bootstrap (Phase 0) error | 64 (config) / 69 (service unavailable) |

For `backfill` and the scheduled multi-product run: aggregate exit = max of per-run exit codes (worst wins).

---

## Implementation Steps

1. **`cli/main.py`** — Typer app:

   ```python
   app = typer.Typer()

   @app.command()
   def run(
       product: str = typer.Option(...),
       week: str | None = typer.Option(None),
       draft_only: bool | None = typer.Option(None),
       dry_run: bool = False,
       force_resend: bool = False,
       config: Path = Path("./config/pulse.yaml"),
   ): ...
   ```

   Each command builds a `RunPlan` via `core.runplan.bootstrap()`, then dispatches to the orchestrator.

2. **Top-level orchestrator** (`pulse/pipeline.py`):

   ```python
   async def execute(plan: RunPlan, store: AuditStore) -> AuditRecord:
       # Insert in_flight row
       # Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 (audit update)
   ```

   Each phase's failure path updates the audit row and re-raises a sentinel that the CLI maps to the right exit code.

3. **`backfill`**:
   - Parse `--weeks A..B` into an ordered list of ISO week strings (handles year boundaries, W53).
   - For each week, build a `RunPlan` and call `execute`.
   - Continue on per-week failure; aggregate worst exit code at the end.

4. **`scheduler/weekly.py`**:
   - Compute last completed ISO week in IST.
   - Load config; iterate `config.products` in alphabetical slug order.
   - For each product, call the in-process equivalent of `pulse run --product <p>`.
   - Per-product wall-clock timeout (default 10 min) so one wedged product doesn't block the rest.
   - Aggregate exit code.

5. **GitHub Actions workflow** (`.github/workflows/weekly.yaml`):

   ```yaml
   on:
     schedule: [{ cron: '30 0 * * 1' }]   # Monday 06:00 IST
     workflow_dispatch: {}                # manual trigger
   jobs:
     run:
       runs-on: ubuntu-latest
       env:
         PULSE_ENV: prod
         ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
         OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/setup-uv@v3
         - run: uv sync
         - name: Restore audit DB
           uses: actions/cache@v4
           with: { path: .pulse/audit.db, key: pulse-audit-${{ github.ref }} }
         - run: uv run python -m pulse.scheduler.weekly --config config/pulse.yaml
         - name: Persist audit DB
           uses: actions/upload-artifact@v4
           with: { name: audit-${{ github.run_id }}, path: .pulse/audit.db }
   ```

   For v1 the audit DB lives in a workflow artifact + cache. Future: object storage.

6. **`pulse mcp probe`** — calls `mcp/client.py::list_tools` against both endpoints; prints a table of expected vs. found tools and exits 0 only if all required tools are present.

7. **Validation guards**:
   - `--week` in the future → reject before bootstrap.
   - `--week` matches the current incomplete week → reject; suggest the previous week.
   - `--force-resend` without `--week` → reject as ambiguous.
   - Both `--dry-run` and `--draft-only` → dry-run dominates; log the override.

---

## Tests to Add

Mapped to [evaluations/phase-8.md](../evaluations/phase-8.md):

- `test_iso_week_in_ist_for_scheduled_tick` (P8-E1) — freezes "now" via `core.clock`.
- `test_run_without_week_flag_uses_default` (P8-E2).
- `test_backfill_iterates_weeks_in_order` (P8-E3) — three weeks, three audit rows.
- `test_one_product_failure_doesnt_block_others` (P8-E4).
- `test_staging_default_draft_only` (P8-E5) — env var `PULSE_ENV=staging`.
- `test_mcp_probe_missing_tool_nonzero_exit` (P8-E6).
- `test_backfill_skips_already_delivered_weeks` (P8-E7).
- `test_current_incomplete_week_rejected` (P8-E8).

Edge cases from [edge-cases/phase-8.md](../edge-cases/phase-8.md):

- DST host running with IST-aware computation; year-boundary backfill (`2025-W52..2026-W02`); cron retry hits idempotency; product added/removed mid-week; conflicting flags; per-product timeout; aggregate exit code policy.

---

## Dependencies

- New libs: none beyond previous phases. `pytest-freezer` for clock-frozen tests.

---

## Definition of Done

- A green run of `weekly.yaml` on a Monday morning produces audit rows + Doc sections + emails (or drafts) for all products.
- `pulse backfill --product groww --weeks 2026-W10..2026-W17` is idempotent: re-running yields all `skipped`.
- `pulse mcp probe` is reliable as a smoke test: green when MCP servers are healthy, red with a useful message otherwise.
- All evaluations P8-E1..E8 pass; edge cases covered.
- Exit-code semantics documented in `README.md` so on-call alerts can be configured.
