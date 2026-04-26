# Phase 1 — Review Ingestion: Edge Cases

Failure modes and boundary conditions for review ingestion.

## Source Behavior

- **App Store RSS feed paginates inconsistently** (some pages return < page-size silently) — do not treat short pages as end-of-feed; check `next` link explicitly.
- **Play Store scraper returns reviews ordered ascending** while App Store returns descending — do not assume ordering; sort by `posted_at` after collection.
- **App Store returns review with `posted_at` in different timezone** than expected — normalize to UTC before window comparison.
- **Play Store review missing `review_id`** (rare but observed) — synthesize stable id from `sha256(author || posted_at || body[:200])`; flag with `synthetic_id=true` in `raw`.
- **Locale variant duplicates**: same review surfaces from `en-IN` and `en-US` storefronts — dedup by `review_id` across locales for the same source.

## Window & Pagination Boundaries

- **Reviews exactly on `window_start` or `window_end` second** → inclusive on both ends, document this clearly.
- **Pagination yields a review just before `window_start`** → stop paginating (newest-first feeds); for oldest-first, skip.
- **Window crosses year boundary** (e.g. window includes both 2025 and 2026 reviews) → handled transparently; no year-bucketing assumed.
- **`max_reviews_per_source` reached mid-page** → respect the cap; record the cap-applied flag so Phase 5 can disclose it.

## Network & Auth

- **HTTP 429** with `Retry-After` header → honor the header value (capped at 60s); do not exceed total retry budget.
- **HTTP 5xx persistent** for a source → soft failure: record per-source `status=failed` in corpus_stats, continue with the other source.
- **HTTP 200 with empty/HTML error body** (e.g. captcha page) → treat as failure; do not parse as JSON and silently yield zero reviews.
- **TLS / cert errors** → fail loudly; do not auto-disable verification.
- **DNS failure** → exponential backoff; if total retry budget exhausted, soft-fail that source.
- **Slow source** that responds but exceeds total time budget → cap with hard timeout; record partial result with `truncated=true`.

## Content Anomalies

- **Review with empty `body` but non-empty `title`** → keep; Phase 2 will merge.
- **Review with empty `body` AND empty `title`** → drop at ingestion (cannot dedup or theme).
- **Review body contains control chars / null bytes** → preserved in `raw`; Phase 2 handles normalization.
- **Extremely long review** (e.g. 10k chars) → keep; downstream phases truncate if needed.
- **Review marked as deleted/hidden** by store but still in feed → drop at ingestion; not actionable.
- **Star-rating outside 1..5** (data-corruption case) → drop with a logged warning; do not coerce.

## Cross-Source Consistency

- **App Store has 200 reviews, Play Store has 5** in the window → both kept; Phase 3 clustering will weight by count, not source.
- **Same user on both stores leaving similar reviews** → dedup across sources is **not** done at Phase 1 (different `review_id` namespaces); Phase 2's `text_hash` dedup handles near-duplicates.

## Recovery

- Phase 1 failure for **one** source: continue, mark missing source in `corpus_stats` and Phase 5 footer.
- Phase 1 failure for **both** sources: abort run, write audit with `status=failed`, `failed_phase=1`. No retry inside the phase beyond per-request backoff — the operator re-runs.
