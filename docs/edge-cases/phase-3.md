# Phase 3 — Embed & Cluster: Edge Cases

Failure modes and boundary conditions for embedding and clustering.

## Corpus Size Boundaries

- **Tiny corpus** (`< min_cluster_size`) → HDBSCAN returns all noise; trigger fallback in Phase 4 (rating-bucketed theming with caveat).
- **One mega-cluster** (corpus is uniform — e.g. only 5-star "great app" reviews) → produce 1 cluster; Phase 4 still runs but report should disclose low diversity.
- **High-noise corpus** (e.g. > 50% noise) → keep top-K from clustered subset; record `noise_ratio` prominently in audit and Phase 5 footer.
- **Massive corpus** (e.g. 5k reviews) → batch embedding API calls; respect rate limits; UMAP scales fine but HDBSCAN may need `min_cluster_size` proportional adjustment.

## Embedding API

- **Embedding API rate limit / 429** → backoff and retry per batch; do not abort the run on transient failures.
- **Embedding API returns wrong dimensionality** (model swapped server-side) → fail loudly; cache key includes model version, so partial cache contamination is prevented.
- **Embedding API returns NaN / inf vectors** → drop those reviews with logged warning; do not poison the centroid math.
- **Partial batch failure** (some reviews error mid-batch) → retry only the failed ones; do not re-embed the successful subset.
- **Cost spike**: corpus much larger than usual → embedding budget cap at run level should preempt this; abort cleanly if exceeded.

## Cache

- **Cache miss after `text_hash` is supposedly stable** → likely a Phase 2 normalization change; investigate, do not just expand cache.
- **Cache corruption** (truncated file) → invalidate and re-embed; do not fail.
- **Cache key collision across models** → impossible if key is `(model_version, text_hash)`; verify this is the schema.

## UMAP / HDBSCAN

- **UMAP non-determinism**: even with `random_state=42`, parallelism can introduce slight jitter. For audit-grade reproducibility, set `n_jobs=1` or accept ε-stable output and hash centroids, not full embeddings.
- **HDBSCAN `min_cluster_size` too high** → all reviews labeled noise; fallback should trigger.
- **HDBSCAN `min_cluster_size` too low** → produces dozens of micro-clusters; ranking still selects top K, but K=5 may exclude meaningful clusters. Tune `min_cluster_size` proportional to corpus size.
- **All embeddings near-identical** (very similar reviews) → UMAP collapses to a point; HDBSCAN may produce 0 clusters. Detect via embedding variance threshold and trigger fallback.

## Ranking

- **Cluster with size = K-th and (K+1)-th tied** → break ties by `avg_rating ascending` (more negative wins) then `cluster_id`.
- **Negativity weight not configurable** → hardcoded; document the formula in audit.
- **Cluster size dominated by bot reviews** → upstream Phase 2 dedup should mitigate; if not, Phase 4 quote validation will surface the repetition.

## Centroid Quote Candidates

- **Centroid review is itself low-quality** (e.g. "good") → still passed; Phase 4's LLM may not select it as a quote, and validation is fine either way.
- **Top-N centroid candidates all near-duplicates** → Phase 4 may produce only 1 distinct quote; theme passes if at least one validated quote remains.

## Recovery

- **Phase 3 entirely fails** (e.g. embedding service down) → run aborts with `failed_phase=3`. No fallback to "skip clustering" — themes without clustering would be ungrounded.
