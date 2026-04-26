"""Phase 3 data models: Cluster and ClusteringResult."""
from __future__ import annotations

from pydantic import BaseModel


class Cluster(BaseModel):
    cluster_id: int
    member_review_ids: list[str]
    size: int
    centroid_review_ids: list[str]  # top-N closest to centroid
    avg_rating: float
    rating_distribution: dict[int, int]


class ClusteringResult(BaseModel):
    clusters: list[Cluster]        # already ranked, top-K
    noise_review_ids: list[str]
    fallback_used: bool
    silhouette: float | None = None  # sampled; None if not computed
