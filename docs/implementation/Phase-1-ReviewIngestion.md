# Phase 1 — Review Ingestion: Implementation

Pull raw public reviews for the configured product from the Apple App Store and Google Play, scoped to the rolling window. Output is a uniform `RawReview[]` regardless of source.

**See also:** [architecture.md § Phase 1](../architecture.md), [evaluations/phase-1.md](../evaluations/phase-1.md), [edge-cases/phase-1.md](../edge-cases/phase-1.md).

---

## Goals

1. Two ingesters (`app_store`, `play_store`) returning a uniform `RawReview` schema.
2. Window filtering applied at the source.
3. Per-source soft failure: one source down does **not** abort the run.
4. Bounded cost: pagination capped, retries bounded, total wall-clock bounded.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/ingestion/base.py` | `Ingester` protocol; retry/backoff helpers; per-source result wrapper |
| `src/pulse/ingestion/app_store.py` | iTunes customer-reviews RSS client (paginated, locale-optional) |
| `src/pulse/ingestion/play_store.py` | Google Play scraper-based client |
| `src/pulse/core/types.py` | Add `RawReview` and `IngestResult` models |

---

## Data Models

```python
class RawReview(BaseModel):
    source: Literal["app_store", "play_store"]
    review_id: str
    product: str
    rating: int = Field(ge=1, le=5)
    title: str | None
    body: str
    author: str | None
    locale: str | None
    posted_at: datetime  # tz-aware UTC
    app_version: str | None
    fetched_at: datetime
    raw: dict             # original payload, kept for audit

class IngestResult(BaseModel):
    source: str
    reviews: list[RawReview]
    pages_fetched: int
    retries: int
    capped: bool
    status: Literal["ok", "empty", "failed"]
    error: str | None = None
```

---

## Library Choices

| Concern | Lib |
|---|---|
| HTTP | `httpx.AsyncClient` |
| Retry/backoff | `tenacity` (`@retry(wait=wait_exponential, stop=stop_after_attempt)`) |
| Play Store | `google-play-scraper` (Python, no Google API) — chosen over the JS lib for in-process use |
| App Store | Custom `httpx` client over the iTunes RSS JSON endpoint |
| Date parsing | `dateutil.parser` for App Store RSS timestamps |

The Play Store package is a scraper, not a Google API client — it does **not** violate the architectural constraint. Document this clearly in the module docstring.

---

## Implementation Steps

1. **Define `Ingester` Protocol**

   ```python
   class Ingester(Protocol):
       async def fetch(self, product: ProductRegistryEntry,
                       window: tuple[datetime, datetime],
                       cap: int) -> IngestResult: ...
   ```

2. **`ingestion/base.py`**: shared utilities
   - `with_backoff()`: tenacity decorator with exponential backoff (1s, 2s, 4s, 8s; max 4 attempts) and respect for `Retry-After` header on 429.
   - `in_window(dt, start, end) -> bool`: inclusive on both ends, UTC-normalized.

3. **`ingestion/app_store.py`**
   - URL pattern: `https://itunes.apple.com/{locale}/rss/customerreviews/page={N}/id={app_id}/sortBy=mostRecent/json`
   - Iterate pages until: page returns no entries, oldest entry is before `window_start`, or `cap` is hit.
   - Map RSS fields → `RawReview`. Preserve raw JSON object in `raw`.
   - Locale handling: default `in` (India). Multi-locale optional (config key) — dedup across locales by `review_id`.

4. **`ingestion/play_store.py`**
   - Wrap `google_play_scraper.reviews(...)` with `lang="en"`, `country="in"` defaults.
   - Use the `continuation_token` to paginate; stop at window or cap.
   - Map response → `RawReview`. Use `reviewId` as `review_id`.

5. **Top-level orchestrator** in a new `ingestion/__init__.py`:

   ```python
   async def ingest(plan: RunPlan, cap_per_source: int) -> dict[str, IngestResult]:
       tasks = {
           src: INGESTERS[src].fetch(plan.product, (plan.window_start, plan.window_end), cap_per_source)
           for src in plan.sources
       }
       return await asyncio.gather(*tasks.values(), return_exceptions=False)
   ```

   Wrap in `try/except` per source so one failure doesn't cancel the other.

6. **Soft-failure semantics**: if a source raises persistently (after retries), return `IngestResult(status="failed", error=...)`. Phase 5 reads `status` to add the footer note.

7. **N_min check**: after all sources return, sum `len(reviews)`. If `total < N_min` AND no source returned `ok`, raise `PhaseFailure(1, "all_sources_empty_or_failed")`. If `total < N_min` but at least one source is `ok`, continue with a warning.

8. **Audit hook**: write per-source counts to a phase-scoped log line; the audit DAO will pick these up in Phase 7.

---

## Tests to Add

Mapped to [evaluations/phase-1.md](../evaluations/phase-1.md):

- `test_app_store_yields_raw_reviews_in_window` (P1-E1) — uses `respx` to mock RSS pages.
- `test_play_store_empty_app_store_30` (P1-E2) — assert run continues, status reflects.
- `test_both_sources_empty_aborts_clean` (P1-E3) — `PhaseFailure(1, ...)`.
- `test_window_filter_drops_outside` (P1-E4).
- `test_429_retries_then_succeeds` (P1-E5) — `respx` returns 429 then 200.
- `test_dedup_by_review_id_within_source` (P1-E6).
- `test_missing_app_version_is_none` (P1-E7).
- `test_cap_enforced` (P1-E8).

Edge cases from [edge-cases/phase-1.md](../edge-cases/phase-1.md):

- App Store short-page pagination, ordering inversions, locale dupes, control chars, captcha-ish HTML body, TLS errors, slow-source timeout.
- Synthesize a deterministic `review_id` from `sha256(author || posted_at || body[:200])` when missing; flag in `raw["synthetic_id"] = True`.

Fixtures: pin a few real-shape responses under `tests/fixtures/reviews/{product}/{week}/` so unit tests are deterministic.

---

## Dependencies

- New libs: `httpx`, `tenacity`, `google-play-scraper`, `python-dateutil`.

---

## Definition of Done

- `pulse ingest --product groww --week 2026-W17` debug command writes a JSONL of `RawReview[]` to `./.pulse/runs/<run_id>/raw.jsonl`.
- All evaluations P1-E1..E8 pass.
- A test using a captured fixture round-trips: same input → same `RawReview[]`.
- Persistent failure of one source surfaces as a soft warning, not an abort, when the other has data.
