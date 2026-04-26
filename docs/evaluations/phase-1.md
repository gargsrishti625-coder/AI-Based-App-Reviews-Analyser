# Phase 1 — Review Ingestion: Evaluations

Evaluation criteria, test cases, and acceptance gates for review ingestion from Apple App Store and Google Play.

## Quality Criteria

- Both ingesters return a uniform `RawReview` regardless of source quirks.
- Window filter (`window_start <= posted_at <= window_end`) is applied at the ingester boundary, not later.
- Transient errors retry with exponential backoff; persistent failure of one source is a soft failure, not a run abort.
- Pagination is exhausted up to a configurable cap (e.g. `max_reviews_per_source`).

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P1-E1 | App Store returns 50 reviews in window | All 50 yielded as `RawReview` with `source="app_store"` |
| P1-E2 | Play Store returns 0 reviews, App Store returns 30 | Run continues; corpus_stats notes Play empty; Phase 5 footer reflects missing source |
| P1-E3 | Both sources empty | Run aborts cleanly with audit `status=skipped`; no Doc append, no email |
| P1-E4 | Source returns reviews outside window | Outside-window reviews dropped; only in-window kept |
| P1-E5 | First request returns HTTP 429 | Backoff and retry; eventual success counts as success |
| P1-E6 | Source returns identical `review_id` twice (paginated overlap) | Dedup by `review_id` within source |
| P1-E7 | Source returns review with missing `app_version` | `RawReview.app_version = None`, no error |
| P1-E8 | Reviews exceed `max_reviews_per_source` cap | Capped; cap-applied flag recorded in `corpus_stats` |

## Determinism / Reproducibility

- For a fixed window with no new reviews, two runs return the same set (modulo `fetched_at`).
- `RawReview.raw` retains the exact original payload byte-for-byte (or normalized JSON) for audit reproducibility.

## Schema Conformance

- Every `RawReview` validates: `rating ∈ {1..5}`, `posted_at` is a timezone-aware datetime, `review_id` is a non-empty string, `body` is a string (may be empty if `title` non-empty).
- `source` enum is exactly `"app_store" | "play_store"`.

## Metrics to Log

- `reviews_fetched_per_source`
- `pagination_pages_per_source`
- `retries_per_source`
- `ingest_duration_ms_per_source`
- `bytes_downloaded_per_source`

## Acceptance Gate

The phase passes when:
1. At least one source returned `>= N_min` reviews (default 20), **or**
2. Both sources are confirmed empty (clean abort path), and the audit row is written.
