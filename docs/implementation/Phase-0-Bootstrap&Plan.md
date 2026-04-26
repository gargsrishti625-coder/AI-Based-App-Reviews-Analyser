# Phase 0 — Bootstrap & Run Plan: Implementation

Build the entrypoint, config loader, and frozen `RunPlan`. After this phase, the rest of the pipeline can assume a validated, immutable plan and reachable MCP servers.

**See also:** [architecture.md § Phase 0](../architecture.md), [evaluations/phase-0.md](../evaluations/phase-0.md), [edge-cases/phase-0.md](../edge-cases/phase-0.md).

---

## Goals

1. Resolve CLI args + YAML config into a single `RunPlan`.
2. Freeze the `RunPlan` so downstream phases cannot mutate it.
3. Probe MCP servers (Docs + Gmail) and verify the required tools exist.
4. Fail fast on any input/config/network issue — no Phase 1 work begins until Phase 0 succeeds.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/cli/main.py` | Typer entrypoint; defines `pulse run`, parses flags, calls `bootstrap()` |
| `src/pulse/config/loader.py` | Reads YAML, validates with Pydantic `PulseConfig`, applies CLI overrides |
| `src/pulse/core/runplan.py` | Builds, validates, and freezes `RunPlan`; exposes a `bootstrap()` orchestrator |
| `src/pulse/core/types.py` | Shared Pydantic models (`PulseConfig`, `ProductRegistryEntry`, `RunPlan`) |
| `src/pulse/mcp/client.py` | Thin MCP host — used here only for `list_tools` probes |

---

## Data Models (Pydantic v2 sketch)

```python
# core/types.py
class ProductRegistryEntry(BaseModel):
    slug: str
    display_name: str
    app_store_id: str | None = None
    play_store_id: str | None = None
    pulse_doc_id: str
    email_recipients: list[EmailStr]

class PulseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    products: dict[str, ProductRegistryEntry]
    window_weeks: int = 8
    n_min_reviews: int = 20
    llm_model: str = "claude-sonnet-4-6"
    embedding_model: str = "text-embedding-3-small"
    mcp: McpEndpoints
    total_token_cap: int = 200_000
    max_reviews_per_source: int = 500
    pulse_env: Literal["dev", "staging", "prod"] = "dev"

class RunPlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: UUID
    product: ProductRegistryEntry
    iso_week: str           # "2026-W17"
    window_start: datetime  # UTC
    window_end: datetime    # UTC
    sources: list[Literal["app_store", "play_store"]]
    llm_model: str
    embedding_model: str
    mcp_docs_endpoint: AnyUrl
    mcp_gmail_endpoint: AnyUrl
    dry_run: bool
    draft_only: bool
```

---

## Implementation Steps (in order)

1. **Scaffold `cli/main.py`** with a Typer app and a `run` command stub that prints the resolved CLI args.
2. **`core/types.py`** — define the models above. Add `extra="forbid"` everywhere so config typos don't silently pass.
3. **`config/loader.py`** — `load_config(path: Path) -> PulseConfig`. Use `yaml.safe_load`, then `PulseConfig.model_validate`. Surface `pydantic.ValidationError` as a CLI error with the offending field path.
4. **ISO week helpers in `core/runplan.py`**:
   - `parse_iso_week(s: str) -> tuple[int, int]` (year, week)
   - `current_iso_week_ist() -> str` — uses `zoneinfo.ZoneInfo("Asia/Kolkata")` and `isocalendar()`; resolves to the **last completed** week (subtract 7 days first, then take ISO week).
   - `iso_week_to_window(iso_week: str, weeks: int) -> tuple[datetime, datetime]`.
5. **`build_runplan(cli, config) -> RunPlan`** — assembles the plan. Defaults `draft_only=True` when `pulse_env in {"dev","staging"}`. Validates: product exists in registry, window fully in the past, sources non-empty.
6. **`mcp/client.py`** — minimal async client: `connect(url) -> Session`, `list_tools() -> list[ToolDescriptor]`. Use the `mcp` Python SDK.
7. **`probe_mcp(plan: RunPlan) -> None`** — connects to both endpoints, verifies the tool surface contains `docs.batchUpdate`, `docs.get`, `gmail.messages.send`, `gmail.drafts.create`. Raises `PhaseFailure(0, ...)` if anything is missing.
8. **`bootstrap(cli) -> RunPlan`** — orchestrates 3+5+7. Wraps everything in a structlog context (`run_id`, `product`, `iso_week`, `phase=0`).
9. **CI grep**: add a `tests/test_no_google_sdk.py` that fails if `googleapiclient` or `google-auth` appears under `src/pulse/`.

---

## Tests to Add

Map directly to [evaluations/phase-0.md](../evaluations/phase-0.md):

- `test_runplan_built_from_valid_inputs` (P0-E1)
- `test_iso_week_resolved_in_ist_for_scheduled_run` (P0-E2)
- `test_draft_only_flag_propagates` (P0-E3)
- `test_dry_run_short_circuits_phase_6` (P0-E4) — assert flag visible in plan
- `test_unknown_product_rejected` (P0-E5 negative)
- `test_mcp_probe_success` and `test_mcp_probe_missing_tool` (P0-E6, P0-E7)
- `test_runplan_is_frozen` (P0-E8) — `pytest.raises(ValidationError)` on mutation

Edge cases from [edge-cases/phase-0.md](../edge-cases/phase-0.md):

- Missing config file, malformed YAML, future ISO week, week 53, MCP timeout, ISO week format variants, conflicting flags, `GOOGLE_OAUTH_TOKEN` warning.

---

## Dependencies

- New libraries: `typer`, `pydantic`, `pyyaml`, `mcp`, `structlog`, `tenacity`.
- Python 3.11+ for `zoneinfo`.

---

## Definition of Done

- `pulse run --product groww --dry-run` exits 0 and logs a JSON line with the full `RunPlan`.
- `pulse mcp probe` exits 0 against a healthy local MCP pair and non-zero with a clear message against a misconfigured one.
- All evaluations P0-E1..E8 pass; all edge cases in `edge-cases/phase-0.md` have a corresponding test.
- The CI invariant grep is green.
