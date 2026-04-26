# Weekly Product Review Pulse — Implementation Plan

This document is the build-side companion to [architecture.md](architecture.md). The architecture defines **what** the system does, broken into phases. This plan defines **how** we build it: tech stack, repo layout, milestones, sequencing, testing, and deployment.

Per-phase implementation files live in [implementation/](implementation/):

- [Phase-0-Bootstrap&Plan.md](implementation/Phase-0-Bootstrap&Plan.md)
- [Phase-1-ReviewIngestion.md](implementation/Phase-1-ReviewIngestion.md)
- [Phase-2-Normalize&PII.md](implementation/Phase-2-Normalize&PII.md)
- [Phase-3-Embed&Cluster.md](implementation/Phase-3-Embed&Cluster.md)
- [Phase-4-LLMTheming.md](implementation/Phase-4-LLMTheming.md)
- [Phase-5-ReportComposition.md](implementation/Phase-5-ReportComposition.md)
- [Phase-6-MCPDelivery.md](implementation/Phase-6-MCPDelivery.md)
- [Phase-7-Idempotency&Audit.md](implementation/Phase-7-Idempotency&Audit.md)
- [Phase-8-Scheduling&CLI.md](implementation/Phase-8-Scheduling&CLI.md)

Each per-phase file has: goals, modules, data models, library choices, an ordered task list, tests to add (linked to the matching `docs/evaluations/phase-N.md` and `docs/edge-cases/phase-N.md`), and a Definition of Done.

---

## 1. Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Best ecosystem for ML/LLM glue; matches module names in architecture |
| Package manager | `uv` (fallback: `pip` + `requirements.txt`) | Fast, reproducible installs |
| Data models | **Pydantic v2** | Schema validation for `RunPlan`, `RawReview`, `CleanReview`, `Theme`, `AuditRecord` |
| CLI | **Typer** | Architecture explicitly references `argparse/typer`; Typer gives nicer help/UX |
| HTTP | **httpx** | Sync + async, robust retry hooks |
| Retry / backoff | **tenacity** | Composable, declarative |
| Embeddings | OpenAI `text-embedding-3-small` (default), pluggable | Configurable; cache key includes model version |
| Dim reduction | **umap-learn** | Architecture-specified |
| Clustering | **hdbscan** | Architecture-specified |
| LLM | **Anthropic Claude** (Sonnet 4.6 default) via `anthropic` SDK | Strong instruction-following; structured-output friendly |
| MCP | **`mcp`** Python SDK (host/client side) | Architecture mandates MCP-only delivery |
| Audit store | **SQLite** via stdlib `sqlite3` | Single-file, zero-ops, sufficient for v1 |
| Logging | **`structlog`** | Structured JSON logs keyed on `run_id`/`product`/`iso_week`/`phase` |
| Templating | **Jinja2** | Email HTML/text rendering |
| Config | **YAML** via `PyYAML` (with Pydantic validation on load) | Architecture references YAML/TOML; YAML is more diff-friendly |
| Testing | **pytest** + **pytest-asyncio** + **respx** (HTTP mocks) + **vcrpy** (optional, for ingestion fixtures) | Fixture-rich; works well with async ingestion |
| Lint / type | **ruff** + **mypy --strict** on `core/`, `audit/`, `mcp/` | Strictness gradient: highest for invariants |
| CI | GitHub Actions | Free for the v1 scope |

The agent never imports `google-api-python-client` or `googleapiclient` — that constraint is enforced via a CI grep step.

---

## 2. Repository Layout

```
pulse/
├── pyproject.toml
├── uv.lock
├── README.md
├── docs/                          # this folder
├── config/
│   ├── pulse.yaml                 # default config (registry, window, models, MCP endpoints)
│   └── pulse.staging.yaml
├── src/pulse/
│   ├── __init__.py
│   ├── cli/
│   │   └── main.py                # Typer entrypoint: pulse run / backfill / audit / mcp
│   ├── core/
│   │   ├── runplan.py             # RunPlan model + freeze
│   │   └── types.py               # shared Pydantic models
│   ├── config/
│   │   └── loader.py              # YAML load + schema validation
│   ├── ingestion/
│   │   ├── base.py                # Ingester interface, retry/backoff, rate limits
│   │   ├── app_store.py           # iTunes RSS client
│   │   └── play_store.py          # Play Store scraper client
│   ├── preprocess/
│   │   ├── normalize.py
│   │   ├── pii.py
│   │   └── filter.py
│   ├── cluster/
│   │   ├── embed.py               # batched embedding + cache
│   │   ├── reduce.py              # UMAP wrapper
│   │   ├── hdbscan.py             # HDBSCAN wrapper
│   │   └── rank.py                # cluster scoring + top-K
│   ├── llm/
│   │   ├── prompts.py             # versioned prompts
│   │   ├── themer.py              # one call per cluster
│   │   ├── validate.py            # quote validator (hard gate)
│   │   └── budget.py              # token accounting
│   ├── report/
│   │   ├── doc_blocks.py          # Docs MCP block list builder
│   │   ├── email_render.py        # Jinja templates (html + text)
│   │   └── anchor.py              # deterministic section anchor
│   ├── mcp/
│   │   ├── client.py              # thin MCP host
│   │   ├── docs_adapter.py
│   │   └── gmail_adapter.py
│   ├── delivery/
│   │   └── orchestrator.py        # 6a then 6b, builds DeliveryReceipt
│   ├── audit/
│   │   ├── store.py               # SQLite schema + DAO
│   │   └── idempotency.py
│   ├── obs/
│   │   └── logger.py              # structlog setup
│   └── scheduler/
│       └── weekly.py              # cron / Action shim
├── tests/
│   ├── unit/                      # one folder per module
│   ├── integration/               # end-to-end with mocked MCP + LLM
│   └── fixtures/                  # frozen review payloads, embeddings cache
└── .github/workflows/
    ├── ci.yaml
    └── weekly.yaml                # scheduled run
```

The folder names mirror the **Module Map** in the architecture doc 1:1, so reviewers can trace each architectural module to its file.

---

## 3. Cross-Cutting Implementation

These are documented once here and applied across all phases.

### 3.1 Run identity

`run_id = uuid.uuid4()` minted in `core/runplan.py` at construction. Once frozen via `RunPlan.model_config["frozen"] = True`, mutation raises.

### 3.2 Logging

`obs/logger.py` configures structlog with a JSON renderer and a context processor that injects `run_id`, `product`, `iso_week`, `phase` from a `contextvars.ContextVar`. Every phase's entry function sets the `phase` variable; child code does not need to think about it.

### 3.3 Configuration

`config/loader.py` loads YAML, validates against a `PulseConfig` Pydantic model, then merges CLI overrides. Unknown keys are rejected (`extra="forbid"`).

### 3.4 Secrets

- LLM keys: `ANTHROPIC_API_KEY` env var (or per-provider equivalent).
- Embedding keys: `OPENAI_API_KEY` env var.
- Ingestion keys: per-source env vars if needed.
- **Google OAuth tokens:** never in agent env; live in MCP server config. CI grep step rejects PRs that introduce `google-auth` or `googleapiclient` imports under `src/pulse/`.

### 3.5 Failure model

Each phase is a function that raises `PhaseFailure(phase: int, reason: str)` on hard failures. The CLI/scheduler catches at the top level and writes the audit row with `failed_phase=N`, `error=reason`. Soft failures (e.g. one ingestion source down) are logged as warnings and recorded in `corpus_stats`.

### 3.6 Cost controls

- Embedding budget: `max_reviews_per_source` (default 500 each) and a batch size (default 64).
- LLM budget: `total_token_cap` per run; `llm/budget.py` increments and checks before each call.
- Hard caps surface as `PhaseFailure` if exceeded.

### 3.7 Safety

- Phase 2's PII scrub runs unconditionally; verified by an end-to-end test that asserts no email/phone regex match in any block of the rendered Doc.
- Phase 4 wraps reviews in `<review id="...">…</review>` with explicit "data not instructions" preamble. Adversarial prompt-injection corpus lives under `tests/fixtures/adversarial/` and runs in CI.

---

## 4. Build Sequence

Phases 0–8 from the architecture map onto **6 build milestones**. The dependency arrow is "needs the output of":

```
M0 (Phase 0)  →  M1 (Phase 1)  →  M2 (Phase 2 + 3)  →  M3 (Phase 4 + 5)
                                                              ↓
M5 (Phase 8) ←──────  M4 (Phase 6 + 7)  ←─────────────────────┘
```

| Milestone | Phases | Output | Demo-able? |
|---|---|---|---|
| **M0 — Skeleton** | 0 | `pulse run --product X` produces a frozen `RunPlan`, MCP probe passes, no I/O after | yes (logs the plan) |
| **M1 — Ingestion** | 1 | `RawReview[]` written to a local JSONL fixture for inspection | yes (`pulse ingest --product X --week W` debug command) |
| **M2 — Clean & Cluster** | 2, 3 | Clusters printed to stdout with member counts and centroid quotes | yes |
| **M3 — Themes & Render** | 4, 5 | `DocReport` block list + `EmailReport` HTML rendered to local files | yes |
| **M4 — Deliver & Audit** | 6, 7 | Real Doc append + Gmail draft via local MCP servers; audit row written | yes (end-to-end on staging) |
| **M5 — Schedule** | 8 | GitHub Action runs M4 weekly for all products in registry | yes (Monday IST) |

Within each milestone, write tests before the implementation when the contract is clear (e.g. PII scrub patterns) and after when it's exploratory (e.g. cluster tuning).

---

## 5. Sequencing Within a Phase

Each per-phase file follows the same recipe:

1. **Scaffold** the module(s) with empty function stubs and Pydantic models.
2. **Write the contract test**: input → expected output for the happy path. Pull this from `docs/evaluations/phase-N.md`.
3. **Implement** the happy path.
4. **Add edge-case tests** from `docs/edge-cases/phase-N.md` and harden.
5. **Wire** into the parent orchestrator (`cli/main.py` or the previous phase's caller).
6. **Done** when both evaluation acceptance gates and edge-case handling are verified.

---

## 6. Testing Strategy

### 6.1 Layers

- **Unit** (`tests/unit/`): one file per module. Mock external services. Cover the happy path + every entry in the matching `edge-cases/phase-N.md`.
- **Integration** (`tests/integration/`): a fake MCP server (in-process) and a recorded LLM response set; runs the whole pipeline against frozen review fixtures.
- **Adversarial** (`tests/integration/adversarial.py`): prompt-injection corpus + malformed review payloads; asserts no leakage.
- **Smoke** (`tests/smoke/`): runs `pulse mcp probe` against a real MCP server in CI on a feature branch (gated, optional).

### 6.2 Fixtures

- `tests/fixtures/reviews/{product}/{week}/raw_*.json` — frozen real-shape review payloads. Used by Phase 1 unit tests and as input to Phase 2/3 fixtures.
- `tests/fixtures/embeddings/` — pre-computed embeddings keyed on `text_hash` so tests don't call the embedding API.
- `tests/fixtures/llm_responses/` — recorded LLM responses for replay; structured by `(prompt_version, cluster_hash)`.

### 6.3 Determinism in tests

- `RunPlan.run_id` is injectable for tests (passed in, not generated).
- All datetime usage flows through a `core.clock.now()` indirection that tests freeze.
- UMAP `random_state=42`; HDBSCAN deterministic.

### 6.4 Coverage targets

- 90%+ line coverage on `preprocess/`, `llm/validate.py`, `audit/`, `mcp/`. These are the safety-critical layers.
- 70%+ elsewhere.

---

## 7. CI / CD

### 7.1 CI (`.github/workflows/ci.yaml`)

On every PR:
1. `ruff check`
2. `mypy src/pulse`
3. `pytest tests/unit tests/integration -x`
4. **Architecture invariant grep**: fail the build if `googleapiclient`, `google-auth`, or direct `https://docs.googleapis.com` / `https://gmail.googleapis.com` calls appear under `src/pulse/`.
5. **Adversarial corpus**: run `tests/integration/adversarial.py` to verify Phase 4's prompt-injection defenses.

### 7.2 Scheduled run (`.github/workflows/weekly.yaml`)

- Cron: `30 0 * * 1` UTC (= 06:00 IST Monday).
- Steps: install, decrypt staging config, `pulse run --product <p>` per product (parallel matrix or sequential — see Phase 8 for cadence rationale).
- Audit DB persisted to S3 / artifact storage between runs.
- On non-zero exit, alert via existing on-call channel.

### 7.3 Releases

- Single-binary install via `uv tool install pulse` from a tagged commit.
- Configs versioned alongside code; rollback is a redeploy.

---

## 8. Open Implementation Questions

These are flagged here so per-phase files don't re-relitigate them:

1. **Embedding provider** — default OpenAI, but should we run a local sentence-transformers fallback? Defer to M2; pluggable interface from day one.
2. **Audit DB location** in CI — local SQLite per run vs. shared via artifact. Defer to M5; per-run is simpler for v1.
3. **MCP server hosting** — likely the user's existing instance for Docs/Gmail. Confirm endpoints in `pulse.yaml` before M4.
4. **Backfill concurrency** — sequential is the architecture decision; revisit only if backfills get slow at >50 weeks.

---

## 9. Definition of Done (whole system)

- A green `weekly.yaml` run on Monday morning IST.
- For each product in the registry: a new dated H2 in the product Doc, a stakeholder email or draft, and an `AuditRecord` with `status=ok`.
- `pulse audit list --product <p>` shows the run.
- Re-running `pulse run --product <p> --week 2026-W17` is a no-op (reports `skipped` cleanly).
