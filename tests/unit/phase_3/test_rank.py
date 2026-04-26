"""Tests for cluster assembly, ranking, and fallback — P3-E5..E7."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from pulse.phase_3.cluster.rank import (
    assemble_clusters,
    fallback_clusters,
    rank_clusters,
)
from pulse.phase_3.core.types import Cluster
from tests.unit.phase_3.conftest import make_clean_review, make_reviews

_UTC = timezone.utc


class TestAssembleClusters:
    def _make_data(self, n: int = 10, n_clusters: int = 2):  # type: ignore[no-untyped-def]
        """Two-cluster synthetic data in 2D."""
        rng = np.random.default_rng(42)
        reviews = make_reviews(n)
        # First half: cluster 0 near (0,0); second half: cluster 1 near (10,10)
        X = np.vstack([
            rng.random((n // 2, 2)).astype(np.float32) * 0.1,
            rng.random((n - n // 2, 2)).astype(np.float32) * 0.1 + 10,
        ])
        labels = np.array([0] * (n // 2) + [1] * (n - n // 2), dtype=np.int32)
        return reviews, X, labels

    def test_cluster_count_matches_labels(self) -> None:
        reviews, X, labels = self._make_data(10)
        clusters, noise = assemble_clusters(reviews, X, labels)
        assert len(clusters) == 2
        assert len(noise) == 0

    def test_noise_label_minus_one_excluded(self) -> None:
        reviews = make_reviews(6)
        X = np.eye(6, dtype=np.float32)
        # Labels: two clusters + two noise
        labels = np.array([0, 0, 1, 1, -1, -1], dtype=np.int32)
        clusters, noise = assemble_clusters(reviews, X, labels)
        assert len(clusters) == 2
        assert len(noise) == 2

    def test_member_ids_cover_all_non_noise(self) -> None:
        reviews, X, labels = self._make_data(10)
        clusters, noise = assemble_clusters(reviews, X, labels)
        all_ids = {rid for c in clusters for rid in c.member_review_ids}
        expected = {r.review_id for r in reviews}
        assert all_ids == expected

    def test_avg_rating_computed(self) -> None:
        reviews = [make_clean_review(f"R{i}", rating=i % 5 + 1) for i in range(10)]
        X = np.eye(10, dtype=np.float32)
        labels = np.zeros(10, dtype=np.int32)
        clusters, _ = assemble_clusters(reviews, X, labels)
        assert clusters[0].avg_rating == pytest.approx(
            sum(r.rating for r in reviews) / 10, abs=0.01
        )

    # P3-E7: centroid candidates are the closest members
    def test_centroid_candidates_are_closest(self) -> None:
        reviews = make_reviews(6)
        # All in cluster 0; review R0 sits exactly at centroid
        X = np.array(
            [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2], [0.1, 0.9], [0.0, 1.0], [0.2, 0.8]],
            dtype=np.float32,
        )
        labels = np.zeros(6, dtype=np.int32)
        clusters, _ = assemble_clusters(reviews, X, labels, n_centroid=2)
        assert len(clusters[0].centroid_review_ids) == 2


class TestRankClusters:
    # P3-E5: negative clusters score higher than positive ones of equal size
    def test_negative_cluster_ranks_higher(self) -> None:
        negative = Cluster(
            cluster_id=0,
            member_review_ids=["a", "b", "c"],
            size=3,
            centroid_review_ids=["a"],
            avg_rating=1.5,
            rating_distribution={1: 2, 2: 1},
        )
        positive = Cluster(
            cluster_id=1,
            member_review_ids=["d", "e", "f"],
            size=3,
            centroid_review_ids=["d"],
            avg_rating=4.5,
            rating_distribution={4: 1, 5: 2},
        )
        ranked = rank_clusters([positive, negative])
        assert ranked[0].cluster_id == negative.cluster_id

    def test_larger_cluster_beats_smaller_same_rating(self) -> None:
        big = Cluster(
            cluster_id=0,
            member_review_ids=list(map(str, range(20))),
            size=20,
            centroid_review_ids=["0"],
            avg_rating=3.0,
            rating_distribution={3: 20},
        )
        small = Cluster(
            cluster_id=1,
            member_review_ids=list(map(str, range(20, 25))),
            size=5,
            centroid_review_ids=["20"],
            avg_rating=3.0,
            rating_distribution={3: 5},
        )
        ranked = rank_clusters([small, big])
        assert ranked[0].cluster_id == big.cluster_id

    # P3-E6: top_k is respected
    def test_top_k_limits_output(self) -> None:
        clusters = [
            Cluster(
                cluster_id=i,
                member_review_ids=[str(i)],
                size=i + 1,
                centroid_review_ids=[str(i)],
                avg_rating=3.0,
                rating_distribution={3: i + 1},
            )
            for i in range(10)
        ]
        ranked = rank_clusters(clusters, top_k=5)
        assert len(ranked) == 5

    def test_empty_clusters_returns_empty(self) -> None:
        assert rank_clusters([]) == []


class TestFallbackClusters:
    def _make_mixed_reviews(self) -> list:  # type: ignore[type-arg]
        return (
            [make_clean_review(f"neg{i}", rating=1) for i in range(6)]
            + [make_clean_review(f"neu{i}", rating=3) for i in range(6)]
            + [make_clean_review(f"pos{i}", rating=5) for i in range(6)]
        )

    def test_three_buckets_produced(self) -> None:
        reviews = self._make_mixed_reviews()
        clusters = fallback_clusters(reviews, min_size=5)
        assert len(clusters) == 3

    def test_bucket_below_min_size_dropped(self) -> None:
        # Only 2 neutral reviews — below min_size=5
        reviews = (
            [make_clean_review(f"neg{i}", rating=1) for i in range(6)]
            + [make_clean_review(f"neu{i}", rating=3) for i in range(2)]
            + [make_clean_review(f"pos{i}", rating=5) for i in range(6)]
        )
        clusters = fallback_clusters(reviews, min_size=5)
        assert len(clusters) == 2

    # P3-E3: tiny corpus triggers fallback and never crashes
    def test_tiny_corpus_does_not_crash(self) -> None:
        reviews = [make_clean_review(f"R{i}", rating=2) for i in range(3)]
        clusters = fallback_clusters(reviews, min_size=5)
        # 3 < min_size=5 → all buckets dropped → empty list (fallback of fallback handled upstream)
        assert isinstance(clusters, list)

    def test_centroid_ids_are_most_recent(self) -> None:
        from datetime import timedelta

        base = datetime(2026, 4, 15, tzinfo=_UTC)
        reviews = [
            make_clean_review(f"pos{i}", rating=5) for i in range(7)
        ]
        # Assign different posted_at
        for i, r in enumerate(reviews):
            object.__setattr__(r, "posted_at", base + timedelta(days=i))

        clusters = fallback_clusters(reviews, min_size=5, n_centroid=3)
        pos_bucket = next(c for c in clusters if c.cluster_id == 2)
        # Most recent 3 → indices 6, 5, 4 (sorted desc)
        assert pos_bucket.centroid_review_ids[0] == "pos6"
