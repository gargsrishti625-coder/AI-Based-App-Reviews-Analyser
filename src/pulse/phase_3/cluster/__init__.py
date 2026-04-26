"""Phase 3 orchestrator: embed → reduce → cluster → rank → ClusteringResult."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import structlog

from pulse.phase_2.core.types import CleanReview
from pulse.phase_3.core.types import ClusteringResult

from .embed import EmbeddingCache, Embedder, embed_reviews
from .hdbscan_ import cluster as hdbscan_cluster
from .rank import assemble_clusters, fallback_clusters, rank_clusters
from .reduce import umap_reduce

log = structlog.get_logger()

_FALLBACK_NOISE_THRESHOLD = 0.8


async def cluster_reviews(
    reviews: list[CleanReview],
    embedder: Embedder,
    cache_path: Path,
    top_k: int = 5,
    n_centroid: int = 5,
    negativity_weight: float = 0.4,
) -> ClusteringResult:
    """Full Phase 3 pipeline for a list of already-cleaned reviews.

    Steps:
    1. Embed (cache-aware).
    2. UMAP reduce to 15D (skipped for tiny/low-dim data).
    3. HDBSCAN cluster.
    4. Assemble Cluster objects; compute noise ratio.
    5. Trigger rating-bucketed fallback when noise_ratio > 0.8 or no clusters.
    6. Rank by negativity-weighted size; keep top_k.
    7. Sampled silhouette score (only on N > 100, non-fallback runs).
    """
    cache = EmbeddingCache(cache_path)

    # 1. Embed
    X, embed_metrics = await embed_reviews(reviews, embedder, cache)
    log.info("phase_3_embedded", **embed_metrics)

    # 2. Reduce
    reduced = umap_reduce(X)

    # 3. Cluster
    n = len(reviews)
    min_cluster_size = max(5, n // 20)
    labels = hdbscan_cluster(reduced, min_cluster_size=min_cluster_size)

    # 4. Assemble
    clusters, noise_ids = assemble_clusters(
        reviews, reduced, labels, n_centroid=n_centroid
    )
    noise_ratio = len(noise_ids) / max(n, 1)

    # 5. Fallback?
    fallback_used = len(clusters) == 0 or noise_ratio > _FALLBACK_NOISE_THRESHOLD
    if fallback_used:
        log.warning(
            "phase_3_fallback_triggered",
            cluster_count=len(clusters),
            noise_ratio=round(noise_ratio, 3),
        )
        clusters = fallback_clusters(
            reviews, min_size=min_cluster_size, n_centroid=n_centroid
        )
        noise_ids = []

    # 6. Rank
    ranked = rank_clusters(clusters, negativity_weight=negativity_weight, top_k=top_k)

    # 7. Silhouette (sampled, non-fallback only)
    silhouette: float | None = None
    if not fallback_used and n > 100:
        try:
            silhouette = _sampled_silhouette(reduced, labels)
        except Exception:
            pass  # never block the pipeline on a diagnostic metric

    log.info(
        "phase_3_done",
        cluster_count=len(ranked),
        noise_count=len(noise_ids),
        fallback=fallback_used,
        silhouette=silhouette,
    )

    return ClusteringResult(
        clusters=ranked,
        noise_review_ids=noise_ids,
        fallback_used=fallback_used,
        silhouette=silhouette,
    )


def _sampled_silhouette(
    X: np.ndarray, labels: np.ndarray, sample: int = 200
) -> float:
    from sklearn.metrics import silhouette_score  # lazy import

    n = X.shape[0]
    if n <= sample:
        return float(silhouette_score(X, labels))
    rng = np.random.default_rng(42)
    idx = rng.choice(n, sample, replace=False)
    return float(silhouette_score(X[idx], labels[idx]))
