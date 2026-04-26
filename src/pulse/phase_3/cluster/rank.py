"""Cluster assembly, ranking, and fallback path."""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from pulse.phase_3.core.types import Cluster

if TYPE_CHECKING:
    from pulse.phase_2.core.types import CleanReview


def _cosine_distances(matrix: np.ndarray, centroid: np.ndarray) -> np.ndarray:
    """Cosine distance (1 − similarity) from each row to *centroid*."""
    row_norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    c_norm = float(np.linalg.norm(centroid))
    safe_row = np.where(row_norms == 0, 1.0, row_norms)
    safe_c = c_norm if c_norm != 0.0 else 1.0
    sims = (matrix / safe_row) @ (centroid / safe_c)
    return 1.0 - sims


def assemble_clusters(
    reviews: list[CleanReview],
    reduced_X: np.ndarray,
    labels: np.ndarray,
    n_centroid: int = 5,
) -> tuple[list[Cluster], list[str]]:
    """Build Cluster objects from HDBSCAN *labels*.

    Returns (clusters, noise_review_ids).  Noise label is -1.
    Centroid candidates are the *n_centroid* members closest by cosine distance
    to the cluster mean vector.
    """
    noise_review_ids = [
        reviews[i].review_id for i, lbl in enumerate(labels) if lbl == -1
    ]
    unique_labels = sorted(set(labels.tolist()) - {-1})

    clusters: list[Cluster] = []
    for label in unique_labels:
        member_indices = [i for i, lbl in enumerate(labels) if lbl == label]
        member_reviews = [reviews[i] for i in member_indices]

        vecs = reduced_X[member_indices]
        centroid = vecs.mean(axis=0)
        distances = _cosine_distances(vecs, centroid)

        top_n = min(n_centroid, len(member_indices))
        closest = np.argsort(distances)[:top_n]
        centroid_ids = [member_reviews[int(i)].review_id for i in closest]

        ratings = [r.rating for r in member_reviews]
        rating_dist: dict[int, int] = {}
        for r in ratings:
            rating_dist[r] = rating_dist.get(r, 0) + 1

        clusters.append(
            Cluster(
                cluster_id=label,
                member_review_ids=[r.review_id for r in member_reviews],
                size=len(member_reviews),
                centroid_review_ids=centroid_ids,
                avg_rating=float(np.mean(ratings)),
                rating_distribution=rating_dist,
            )
        )

    return clusters, noise_review_ids


def rank_clusters(
    clusters: list[Cluster],
    negativity_weight: float = 0.4,
    top_k: int = 5,
) -> list[Cluster]:
    """Score = size × (1 + negativity_weight × (5 − avg_rating)).

    Higher score → higher rank.  Returns at most *top_k* clusters.
    """

    def _score(c: Cluster) -> float:
        return c.size * (1.0 + negativity_weight * (5.0 - c.avg_rating))

    return sorted(clusters, key=_score, reverse=True)[:top_k]


def fallback_clusters(
    reviews: list[CleanReview],
    min_size: int = 5,
    n_centroid: int = 5,
) -> list[Cluster]:
    """Rating-bucketed pseudo-clusters when HDBSCAN produces nothing useful.

    Three buckets: negative (1–2★), neutral (3★), positive (4–5★).
    Any bucket below *min_size* is dropped.
    Centroid candidates are the most-recently-posted *n_centroid* members.
    """
    buckets: list[tuple[int, int, int]] = [
        (0, 1, 2),  # (cluster_id, rating_low, rating_high)
        (1, 3, 3),
        (2, 4, 5),
    ]

    clusters: list[Cluster] = []
    for cid, low, high in buckets:
        members = [r for r in reviews if low <= r.rating <= high]
        if len(members) < min_size:
            continue

        members.sort(key=lambda r: r.posted_at, reverse=True)
        centroid_ids = [r.review_id for r in members[:n_centroid]]

        ratings = [r.rating for r in members]
        rating_dist: dict[int, int] = {}
        for r in ratings:
            rating_dist[r] = rating_dist.get(r, 0) + 1

        clusters.append(
            Cluster(
                cluster_id=cid,
                member_review_ids=[r.review_id for r in members],
                size=len(members),
                centroid_review_ids=centroid_ids,
                avg_rating=float(np.mean(ratings)),
                rating_distribution=rating_dist,
            )
        )

    return clusters
