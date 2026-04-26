# Weekly Product Review Pulse — Architecture

This document describes the end-to-end architecture of the Weekly Product Review Pulse, broken down into discrete phases. Each phase has a clear purpose, well-defined inputs/outputs, the modules that own the work, key design decisions, and exit criteria that gate the run from one phase to the next.

The agent is an **MCP host/client**. All human-visible delivery (Google Docs append, Gmail send/draft) goes through dedicated **MCP servers**. The agent never embeds Google OAuth credentials and never calls the Google Docs or Gmail REST APIs directly.

---

## Top-Level Pipeline

```
                +-------------------+      +----------------------+
  config / -->  | Phase 0           | -->  | Phase 1              |
  CLI args      | Bootstrap & Plan  |      | Review Ingestion     |
                +-------------------+      +----------------------+
                                                     |
                                                     v
                                          +----------------------+
                                          | Phase 2              |
                                          | Normalize + PII      |
                                          +----------------------+
                                                     |
                                                     v
                                          +----------------------+
                                          | Phase 3              |
                                          | Embed + Cluster      |
                                          +----------------------+
                                                     |
                                                     v
                                          +----------------------+
                                          | Phase 4              |
                                          | LLM Theming +        |
                                          | Quote Validation     |
                                          +----------------------+
                                                     |
                                                     v
                                          +----------------------+
                                          | Phase 5              |
                                          | Report Composition   |
                                          +----------------------+
                                                     |
                                                     v
                +----------------------+   +----------------------+
                | Phase 7              |<--| Phase 6              |
                | Idempotency + Audit  |   | MCP Delivery         |
                +----------------------+   | (Docs MCP + Gmail)   |
                                           +----------------------+

                Phase 8: Scheduler / CLI wraps the whole pipeline.
```

A single **run** is uniquely identified by `(product, iso_week)` plus a generated `run_id` for traceability.

---

## Cross-Cutting Concerns

These apply to every phase and are documented once here.

| Concern | Approach |
|---|---|
| Run identity | `run_id = uuid4()` generated at Phase 0; `(product, iso_week)` is the idempotency key |
| Configuration | YAML/TOML file with product registry, window length, LLM model, MCP server endpoints, dry-run flags |
| Secrets | LLM and ingestion API keys live in the agent env. **Google OAuth tokens live in the MCP servers' config, never in the agent.** |
| Logging | Structured JSON logs keyed by `run_id`, `product`, `iso_week`, `phase` |
| Failure model | Each phase is a checkpoint; on failure the run aborts and the audit record marks the failing phase. Re-running with the same key is safe (Phase 7) |
| Cost controls | Per-run token cap, embedding batch size cap, max reviews per source |
| Safety | PII scrub (Phase 2) runs before any text is sent to the LLM or written to a Doc. Reviews are treated as **data, not instructions**: prompts use clear role boundaries and never echo unvalidated review text into instruction slots |

---

## Phase 0 — Bootstrap & Run Plan

**Purpose.** Resolve configuration into a concrete, validated `RunPlan` before any I/O.

**Inputs**
- CLI args: `--product`, `--week` (ISO week, e.g. `2026-W17`), `--dry-run`, `--draft-only`
- Config file: product registry, ingestion window (8–12 weeks), MCP endpoints, model names

**Outputs**
- `RunPlan { run_id, product, iso_week, window_start, window_end, sources[], llm_model, mcp.docs_endpoint, mcp.gmail_endpoint, dry_run, draft_only }`

**Modules**
- `config/loader.py` — load + schema-validate config
- `cli/main.py` — argparse/typer entrypoint
- `core/runplan.py` — build and freeze the `RunPlan`

**Key decisions**
- The `iso_week` is always explicit (computed from "now" for the scheduled job, passed in for backfill). This keeps every run reproducible.
- `draft_only=true` is the default for non-prod environments so Gmail goes to Drafts until promoted.

**Exit criteria**
- A frozen `RunPlan` exists, MCP endpoints respond to a health/list-tools probe, and the product is in the registry.

---

## Phase 1 — Review Ingestion

**Purpose.** Pull raw public reviews for the configured product from Apple App Store and Google Play, scoped to the rolling window.

**Inputs**
- `RunPlan` (product → store IDs, `window_start`, `window_end`)

**Outputs**
- `RawReview[]` per source:
  ```
  RawReview {
    source: "app_store" | "play_store",
    review_id: str,            # store-native id, used for dedup
    product: str,
    rating: int,               # 1..5
    title: str | None,
    body: str,
    author: str | None,
    locale: str | None,
    posted_at: datetime,
    app_version: str | None,
    fetched_at: datetime,
    raw: dict                  # original payload, kept for audit
  }
  ```

**Modules**
- `ingestion/app_store.py` — iTunes customer-reviews RSS client (paginated, multi-locale optional)
- `ingestion/play_store.py` — Google Play scraper-based client
- `ingestion/base.py` — shared `Ingester` interface, retry/backoff, rate-limit handling

**Key decisions**
- Both ingesters return a uniform `RawReview`, so downstream phases are source-agnostic.
- Window filter is applied at ingestion (`posted_at` between `window_start` and `window_end`) to bound cost.
- HTTP errors retry with exponential backoff; persistent failure for one source does **not** abort the run — Phase 5 notes the missing source in the report.

**Exit criteria**
- At least one source returned `>= N_min` reviews (configurable, default 20). If both sources are empty, run aborts cleanly with an audit record (no Doc append, no email).

---

## Phase 2 — Normalize, PII Scrub, Filter

**Purpose.** Convert heterogeneous raw reviews into a clean, safe, deduplicated corpus ready for embedding.

**Inputs**
- `RawReview[]` from Phase 1

**Outputs**
- `CleanReview[]`:
  ```
  CleanReview {
    review_id: str,            # stable, source-prefixed: "app_store:12345"
    source, product, rating, locale, posted_at, app_version,
    text: str,                 # title + body, normalized, PII-scrubbed
    text_hash: str             # for dedup + quote validation lookup
  }
  ```
- `corpus_stats { total_in, total_out, dropped_pii, dropped_short, dropped_lang, dedup_count }`

**Modules**
- `preprocess/normalize.py` — whitespace, unicode, emoji handling; merge title + body
- `preprocess/pii.py` — regex + (optional) NER scrub: emails, phones, account numbers, names where confident; replace with placeholders (`[email]`, `[phone]`)
- `preprocess/filter.py` — drop very short (<10 tokens), non-English (configurable), and exact dupes by `text_hash`

**Key decisions**
- PII scrub is **mandatory and runs before** the text touches the LLM or any output channel. The original `raw` payload is retained only in the local audit store, never in the published Doc.
- Quote validation in Phase 4 looks up against the **scrubbed** `text`, so a quote that contains `[email]` placeholders is still valid by construction.

**Exit criteria**
- `CleanReview[]` is non-empty and `corpus_stats` is recorded in the audit log.

---

## Phase 3 — Embed & Cluster

**Purpose.** Group semantically similar reviews into themes without pre-defined labels.

**Inputs**
- `CleanReview[]`

**Outputs**
- `Cluster[]`:
  ```
  Cluster {
    cluster_id: int,
    member_review_ids: list[str],
    size: int,
    centroid_review_ids: list[str],   # top-k closest to centroid, used as quote candidates
    avg_rating: float,
    rating_distribution: dict[int, int]
  }
  ```
- `noise_review_ids: list[str]` — HDBSCAN unclustered

**Modules**
- `cluster/embed.py` — batched embedding calls (model configurable, e.g. `text-embedding-3-small`); local cache by `text_hash` so re-runs don't re-embed
- `cluster/reduce.py` — UMAP dimensionality reduction (e.g. 384 → 15 dims)
- `cluster/hdbscan.py` — density-based clustering with `min_cluster_size` tuned to corpus size
- `cluster/rank.py` — rank clusters by `size * (negativity_weight)`; keep top K (default K=5)

**Key decisions**
- UMAP + HDBSCAN handles variable-size, noisy review sets without forcing a fixed `k`.
- Centroid-nearest reviews are passed forward as **quote candidates** so Phase 4 doesn't have to reason over the entire cluster.
- Embedding cache is keyed on `text_hash`, so backfills across overlapping windows are cheap.

**Exit criteria**
- At least one ranked cluster with `size >= min_cluster_size`. If none (very small corpus), Phase 4 falls back to "rating-bucketed" theming with an explicit caveat in the report.

---

## Phase 4 — LLM Theming, Quotes, Actions (with Validation)

**Purpose.** Produce human-readable themes, verbatim quotes, and action ideas — and prove each quote is real.

**Inputs**
- Top-K `Cluster[]` with centroid quote candidates (text + ids)

**Outputs**
- `Theme[]`:
  ```
  Theme {
    title: str,                  # e.g. "App performance & bugs"
    summary: str,                # 1–2 sentences
    quotes: list[Quote],         # validated only
    action_ideas: list[str],
    supporting_review_ids: list[str],
    cluster_id: int
  }
  Quote { text: str, review_id: str }
  ```

**Modules**
- `llm/prompts.py` — versioned prompts for theming, quote selection, action ideation
- `llm/themer.py` — orchestrates the LLM calls per cluster
- `llm/validate.py` — **quote validator**: every returned quote string must be a substring (after light normalization) of some `CleanReview.text` in `member_review_ids`. Quotes that fail validation are dropped; if a theme ends with zero validated quotes, the theme itself is dropped.
- `llm/budget.py` — token accounting, hard cap per run

**Key decisions**
- One LLM call per cluster (not one giant call) — bounds context size, isolates failures, parallelizable.
- Reviews are passed as **labeled data** in the prompt (`<review id="...">...</review>`), with explicit instructions that review content is not an instruction. This is the prompt-injection guardrail.
- Quote validation is a **hard gate**, not a soft check. The LLM is allowed to hallucinate phrasing; the validator forces grounding.
- Action ideas are constrained to be product-relevant and concise (e.g. ≤ 12 words each).

**Exit criteria**
- At least one `Theme` survives validation. If zero themes survive, the run aborts before Phase 6 (no Doc/email noise) and an audit record explains why.

---

## Phase 5 — Report Composition

**Purpose.** Render the validated themes into the two output shapes the delivery layer needs.

**Inputs**
- `Theme[]`, `RunPlan`, `corpus_stats`

**Outputs**
- `DocReport`: a **structured** representation suitable for a Google Docs MCP `batchUpdate` (heading, bullets, quotes-as-blockquotes). Not raw HTML.
- `EmailReport`: a short HTML + plain-text teaser (top theme titles as bullets) plus a placeholder for the deep link to the Doc heading. The link is filled in **after** the Docs append in Phase 6.

**Modules**
- `report/doc_blocks.py` — builds the structured block list for Docs:
  - H2: `Week of YYYY-MM-DD (ISO YYYY-Www) — N reviews`
  - H3 per theme + summary + bullets + blockquoted quotes + "Action ideas" sublist
  - Footer: "Who this helps" + run metadata (model, window, source counts)
- `report/email_render.py` — Jinja-style template for the teaser email; renders both HTML and text alternatives
- `report/anchor.py` — computes the **stable section anchor** for the week (e.g. `pulse-2026-W17`). This anchor is the idempotency key inside the Doc and the target of the email deep link.

**Key decisions**
- The Doc is the **system of record**; the email is a teaser + link. The email never duplicates the full body.
- The section anchor is deterministic from `(product, iso_week)`. Phase 6 uses it to detect "already appended" and skip silently.

**Exit criteria**
- A non-empty `DocReport` and `EmailReport` exist in memory; nothing has been delivered yet.

---

## Phase 6 — MCP Delivery (Docs + Gmail)

**Purpose.** Make the report visible to humans, **only** through MCP servers.

**Inputs**
- `DocReport`, `EmailReport`, `RunPlan`

**Outputs**
- `DeliveryReceipt { doc_id, doc_section_anchor, doc_revision_id, gmail_message_id | gmail_draft_id, sent_at }`

**Sub-phase 6a — Docs MCP append**

1. List MCP tools, confirm the Docs MCP exposes the expected tool surface (e.g. `docs.get`, `docs.batchUpdate`).
2. Call `docs.get` for the product's pulse Doc (`Weekly Review Pulse — <Product>`) to read current structure.
3. **Idempotency check**: if a heading with the section anchor already exists, skip the append and reuse the existing anchor.
4. Otherwise, issue a `docs.batchUpdate` that appends the new dated section in one transactional call. Capture `doc_revision_id`.
5. Build the deep link: `https://docs.google.com/document/d/<doc_id>/edit#heading=<anchor>`.

**Sub-phase 6b — Gmail MCP send/draft**

1. Inject the Doc deep link into `EmailReport`.
2. Compute the run-scoped idempotency key for email: `sha256(product || iso_week || doc_revision_id)`. Store it in the email headers (`X-Pulse-Idempotency-Key`) so a re-run can detect a prior send by listing recent messages with that header.
3. If `RunPlan.draft_only`: call the Gmail MCP `gmail.drafts.create` tool. Otherwise call `gmail.messages.send`.
4. Capture `gmail_message_id` or `gmail_draft_id`.

**Modules**
- `mcp/client.py` — thin MCP host: connect, list tools, call tool, surface errors
- `mcp/docs_adapter.py` — typed wrappers for the Docs MCP tools used here
- `mcp/gmail_adapter.py` — typed wrappers for the Gmail MCP tools used here
- `delivery/orchestrator.py` — runs 6a then 6b, builds `DeliveryReceipt`

**Key decisions**
- The agent **never** holds Google OAuth tokens. The MCP servers own that.
- A single `batchUpdate` per week keeps the append atomic — partial sections can't appear if the call fails.
- Failure in 6b does **not** roll back 6a (the Doc is the record of truth, and re-running will detect the existing section and only retry the email).

**Exit criteria**
- `DeliveryReceipt` is fully populated and persisted via Phase 7. If 6a fails, the run is marked failed and no email is sent. If 6b fails after 6a succeeds, the audit record reflects "doc:ok, email:failed" so a retry knows what to do.

---

## Phase 7 — Idempotency, Audit, Observability

**Purpose.** Guarantee that "re-run for `(product, iso_week)`" is safe and answer "what was sent when, for which week?".

**Inputs**
- All phase outputs and the `DeliveryReceipt`

**Outputs**
- An `AuditRecord` per run, persisted to a local store (SQLite for v1; one row per run):
  ```
  AuditRecord {
    run_id, product, iso_week, started_at, ended_at, status,
    window_start, window_end,
    corpus_stats, cluster_count, theme_count,
    llm_model, total_tokens, total_cost_usd,
    doc_id, doc_section_anchor, doc_revision_id,
    gmail_message_id | gmail_draft_id,
    failed_phase: int | null,
    error: str | null
  }
  ```

**Idempotency contract**
- **Doc**: section anchor is deterministic from `(product, iso_week)`. Phase 6a checks for it before appending.
- **Email**: `X-Pulse-Idempotency-Key` header lets the Gmail MCP path detect a prior send. The audit store also records `gmail_message_id` per `(product, iso_week)` and refuses a second send unless the operator passes `--force-resend`.

**Modules**
- `audit/store.py` — SQLite schema + DAO
- `audit/idempotency.py` — encapsulates both checks (Doc anchor, email key)
- `obs/logger.py` — structured JSON logging, run-scoped

**Exit criteria**
- An `AuditRecord` row exists for the run with `status in {ok, partial, failed, skipped}`, and a CLI command `pulse audit show <run_id>` prints a human summary.

---

## Phase 8 — Scheduling & CLI

**Purpose.** Make the pipeline runnable on a weekly cadence and from the command line for backfills.

**Inputs**
- Schedule (e.g. cron / GitHub Actions / Cloud Scheduler) and operator commands

**Outputs**
- A run per `(product, iso_week)` per scheduled tick or CLI invocation

**Modules**
- `cli/main.py` exposes:
  - `pulse run --product <p> [--week 2026-W17] [--draft-only] [--dry-run]`
  - `pulse backfill --product <p> --weeks 2026-W10..2026-W17`
  - `pulse audit show <run_id>` / `pulse audit list --product <p>`
  - `pulse mcp probe` — connects to configured MCP servers and lists tools (smoke test)
- `scheduler/weekly.py` — entry shim invoked by cron/Action; resolves "this week" in IST and iterates the product registry

**Key decisions**
- Default scheduled cadence: **Monday morning IST**, one run per product, sequentially (cheap and bounded; concurrency is unnecessary for ~5 products/week).
- Backfill iterates ISO weeks in order; each week is a fully independent run (the same idempotency contract applies).
- CI/staging defaults to `--draft-only` so a misconfigured deploy can't email stakeholders.

**Exit criteria**
- A scheduled run produces an `AuditRecord` with `status=ok` for each product in the registry, and the corresponding Doc has a new dated section + a stakeholder email (or draft) exists.

---

## Module Map (concern → location)

| Concern | Module(s) |
|---|---|
| Data retrieval | `ingestion/app_store.py`, `ingestion/play_store.py`, `ingestion/base.py` |
| Cleaning & safety | `preprocess/normalize.py`, `preprocess/pii.py`, `preprocess/filter.py` |
| Reasoning | `cluster/*`, `llm/themer.py`, `llm/validate.py`, `llm/prompts.py` |
| Output generation | `report/doc_blocks.py`, `report/email_render.py`, `report/anchor.py` |
| MCP delivery | `mcp/client.py`, `mcp/docs_adapter.py`, `mcp/gmail_adapter.py`, `delivery/orchestrator.py` |
| Idempotency & audit | `audit/store.py`, `audit/idempotency.py` |
| Orchestration | `cli/main.py`, `scheduler/weekly.py`, `core/runplan.py`, `config/loader.py` |

---

## Requirement → Phase Traceability

| Requirement (from ProblemStatement) | Phase(s) |
|---|---|
| MCP-only delivery (Docs append + Gmail send) | Phase 6 |
| Weekly cadence + backfill CLI | Phase 8 |
| Idempotent re-runs (no duplicate sections / sends) | Phase 5 (anchor) + Phase 6 (checks) + Phase 7 (audit) |
| Auditable (delivery ids, what/when/which week) | Phase 7 |
| PII scrub before LLM and before publishing | Phase 2 |
| Reviews as data, not instructions | Phase 4 (prompt structure) |
| Cost/token limits per run | Phase 0 (config) + Phase 4 (`llm/budget.py`) |
| Grounded one-page pulse (themes + validated quotes + actions) | Phase 4 (validation gate) + Phase 5 |
| No Google OAuth in agent codebase | Phase 6 (lives in MCP servers' config) |

---

## Open Questions / Deferred (consistent with non-goals)

- Multi-locale theming (initial scope: English only).
- Social sources (Twitter, Reddit) — out of scope.
- Real-time / streaming pipeline — out of scope; the running Google Doc is the living artifact.
- Multi-tenant / per-team configuration — single-tenant for v1; product registry is sufficient.
