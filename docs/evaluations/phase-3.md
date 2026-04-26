# Phase 3 — Embed & Cluster: Evaluations

Evaluation criteria, test cases, and acceptance gates for embedding and clustering.

## Quality Criteria

- Clusters are **semantically coherent**: members of the same cluster share a topic (e.g. "login bugs", "rewards confusion"), not just keyword overlap.
- The pipeline runs without `k` being specified — HDBSCAN discovers cluster count.
- Embedding cache hits dominate on overlapping windows (cost amortization).
- Centroid-nearest reviews are reliable quote candidates for Phase 4.

## Functional Tests

| ID | Scenario | Expected |
|---|---|---|
| P3-E1 | 200 reviews split across 4 obvious topics (synthetic) | HDBSCAN finds 3–6 clusters; each cluster's centroid review aligns with the seeded topic |
| P3-E2 | Re-run on the same `CleanReview[]` | Embedding cache hit rate ≈ 100%; clustering output identical (modulo non-determinism in UMAP — see edge cases) |
| P3-E3 | Tiny corpus (e.g. 15 reviews) | Either produces 1 small cluster OR triggers fallback (Phase 4 rating-bucketed) — never crashes |
| P3-E4 | All reviews identical text | All collapse upstream in Phase 2 dedup; Phase 3 sees 1 review and triggers fallback |
| P3-E5 | Cluster ranking by `size * negativity_weight` | Clusters with low `avg_rating` rise; ties broken by size |
| P3-E6 | Top-K selection (default K=5) | Top 5 clusters returned; `noise_review_ids` populated separately |
| P3-E7 | Centroid quote candidates | Top-N nearest reviews to centroid by cosine distance, N configurable (default 5) |

## Cluster Quality Heuristics

- **Silhouette score** on the reduced embedding space: aim for > 0.2 average for clusters with `size >= min_cluster_size`. Below that, log a warning (clusters may be weak).
- **Intra-cluster rating variance**: should be lower than corpus-wide variance for "issue" clusters (negative reviews tend to share root cause).
- **Topic separation**: top-3 cluster centroids should be > θ apart in cosine distance (θ tunable, e.g. 0.15).

## Determinism / Reproducibility

- Embedding model + version is recorded in audit; cache key includes model version to prevent cross-model cache poisoning.
- UMAP seed is fixed (`random_state=42`) for reproducibility.
- HDBSCAN is deterministic given fixed input.

## Metrics to Log

- `embedding_cache_hits`, `embedding_cache_misses`, `embedding_api_calls`
- `umap_duration_ms`, `hdbscan_duration_ms`
- `cluster_count`, `noise_count`, `noise_ratio = noise / total`
- `top_k_cluster_sizes` and `avg_ratings`
- `silhouette_score` (sampled)

## Acceptance Gate

The phase passes when:
1. At least one cluster has `size >= min_cluster_size`, **or**
2. The fallback flag is set so Phase 4 uses rating-bucketed theming with an explicit caveat.
