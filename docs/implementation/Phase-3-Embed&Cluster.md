# Phase 3 — Embed & Cluster: Implementation

Group semantically similar reviews into themes without pre-defined labels. Output is a ranked `Cluster[]` plus a noise list, with centroid-nearest reviews flagged as quote candidates.

**See also:** [architecture.md § Phase 3](../architecture.md), [evaluations/phase-3.md](../evaluations/phase-3.md), [edge-cases/phase-3.md](../edge-cases/phase-3.md).

---

## Goals

1. Produce embeddings via a configurable model with a local cache keyed on `(model_version, text_hash)`.
2. Reduce dimensionality with UMAP (deterministic seed).
3. Cluster with HDBSCAN; do not require `k`.
4. Rank clusters by `size * negativity_weight`; keep top K.
5. Mark top-N centroid-nearest reviews per cluster as quote candidates for Phase 4.

---

## Modules

| File | Responsibility |
|---|---|
| `src/pulse/cluster/embed.py` | Batched embedding API calls + on-disk cache |
| `src/pulse/cluster/reduce.py` | UMAP wrapper |
| `src/pulse/cluster/hdbscan.py` | HDBSCAN wrapper |
| `src/pulse/cluster/rank.py` | Cluster scoring, top-K selection, fallback decision |
| `src/pulse/core/types.py` | Add `Cluster` |

---

## Data Models

```python
class Cluster(BaseModel):
    cluster_id: int
    member_review_ids: list[str]
    size: int
    centroid_review_ids: list[str]    # top-N closest to centroid
    avg_rating: float
    rating_distribution: dict[int, int]

class ClusteringResult(BaseModel):
    clusters: list[Cluster]            # already ranked, top-K
    noise_review_ids: list[str]
    fallback_used: bool
    silhouette: float | None           # sampled
```

---

## Library Choices

| Concern | Lib |
|---|---|
| Embedding | `openai` SDK (default `text-embedding-3-small`); pluggable `Embedder` Protocol so a local `sentence-transformers` model can swap in |
| UMAP | `umap-learn` |
| Clustering | `hdbscan` |
| Caching | sqlite (one table) — same DB file as audit, separate table |
| Vectors | `numpy` |

---

## Embedding Cache Schema

```sql
CREATE TABLE IF NOT EXISTS embedding_cache (
  model_version TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  vector BLOB NOT NULL,        -- numpy float32 array, .tobytes()
  dim INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (model_version, text_hash)
);
```

The cache key includes `model_version` so a config change cannot poison cached vectors of the previous model.

---

## Implementation Steps

1. **`Embedder` Protocol** in `cluster/embed.py`:

   ```python
   class Embedder(Protocol):
       model_version: str
       dim: int
       async def embed_batch(self, texts: list[str]) -> np.ndarray: ...
   ```

   Concrete `OpenAIEmbedder` wraps `client.embeddings.create(model=..., input=batch)` with `tenacity` backoff.

2. **`embed_reviews(reviews: list[CleanReview], embedder) -> np.ndarray`**:
   - Look up each `text_hash` in cache.
   - Batch the misses (default `batch_size=64`).
   - Persist new vectors to cache transactionally.
   - Return an `(N, D)` array aligned to the input order.
   - Track `cache_hits`, `cache_misses`, `api_calls` for metrics.

3. **`reduce.py`**: `umap_reduce(X: np.ndarray, n_components=15, random_state=42, n_neighbors=15) -> np.ndarray`. Set `n_jobs=1` for reproducibility.

4. **`hdbscan_cluster.py`**: `cluster(X: np.ndarray, min_cluster_size: int) -> np.ndarray` returning labels (`-1` for noise). `min_cluster_size` is a function of corpus size: `max(5, len(X) // 20)`.

5. **Cluster assembly** in `rank.py`:
   - Build `Cluster` objects from labels + reviews.
   - Compute centroid per cluster (mean of cluster's reduced vectors).
   - Pick top-N (default 5) member reviews by cosine distance to centroid → `centroid_review_ids`.
   - Compute `avg_rating`, `rating_distribution`.

6. **Ranking**: score = `size * (1 + negativity_weight * (5 - avg_rating))`. Default `negativity_weight=0.4` (configurable). Sort descending, keep top K.

7. **Fallback path**: if `len(clusters) == 0` or `noise_ratio > 0.8`:
   - Set `fallback_used=True`.
   - Synthesize "rating-bucketed" pseudo-clusters: `1-2 stars`, `3 stars`, `4-5 stars`, dropping any below `min_cluster_size`. Centroid candidates for each = first 5 reviews sorted by `posted_at desc`.
   - Phase 4 / Phase 5 will surface a caveat in the report.

8. **Silhouette sample** (optional, gated): on corpora over 100, sample 200 points and compute mean silhouette. Log only.

---

## Tests to Add

Mapped to [evaluations/phase-3.md](../evaluations/phase-3.md):

- `test_synthetic_topics_cluster_correctly` (P3-E1) — 4 seeded topic groups, assert HDBSCAN finds 3–6 clusters and centroids align.
- `test_rerun_uses_cache` (P3-E2) — second call yields zero `api_calls`.
- `test_tiny_corpus_triggers_fallback` (P3-E3).
- `test_all_dedup_to_one_review` (P3-E4) — fallback path exercised.
- `test_ranking_prefers_negative` (P3-E5).
- `test_top_k_returned_and_noise_separate` (P3-E6).
- `test_centroid_candidates_are_closest` (P3-E7).

Edge cases from [edge-cases/phase-3.md](../edge-cases/phase-3.md):

- Embedding API 429 backoff, partial-batch failure, NaN vectors dropped, cache-key collision across models, UMAP determinism with `n_jobs=1`, `min_cluster_size` too high produces all-noise (fallback fires).

Use **frozen embeddings** in `tests/fixtures/embeddings/` so unit tests don't need network or randomness.

---

## Dependencies

- New libs: `openai`, `numpy`, `umap-learn`, `hdbscan`, `scikit-learn` (transitive).

---

## Definition of Done

- `pulse cluster --run-id <id>` debug command writes `clusters.json` and prints a summary.
- A second run on the same corpus completes with cache_hit_rate ≈ 100%.
- All evaluations P3-E1..E7 pass.
- The fallback path is exercised by at least two tests (small corpus, high-noise corpus).
