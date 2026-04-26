"""Integration tests for Phase 3 cluster_reviews orchestrator — P3-E1..E4."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from pulse.phase_3.cluster import cluster_reviews
from tests.unit.phase_3.conftest import FakeEmbedder, make_clean_review, make_reviews


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(coro)


def _well_separated_embedder(n: int, n_topics: int = 4, dim: int = 8) -> FakeEmbedder:
    """Build a FakeEmbedder whose vectors form *n_topics* well-separated clusters."""
    rng = np.random.default_rng(0)
    # Topic centres are far apart (distance >> within-cluster variance)
    centres = rng.random((n_topics, dim)).astype(np.float32) * 20
    per_topic = n // n_topics
    vecs_list = []
    for i in range(n_topics):
        noise = rng.random((per_topic, dim)).astype(np.float32) * 0.05
        vecs_list.append(centres[i] + noise)
    # Pad remainder to the first topic
    remainder = n - per_topic * n_topics
    if remainder:
        noise = rng.random((remainder, dim)).astype(np.float32) * 0.05
        vecs_list.append(centres[0] + noise)
    return FakeEmbedder(np.vstack(vecs_list))


class TestClusterReviews:
    # P3-E1: well-separated synthetic topics → clusters found, not fallback
    def test_synthetic_topics_cluster_correctly(self, tmp_path: Path) -> None:
        n = 80  # 4 topics × 20 reviews each
        reviews = make_reviews(n)
        embedder = _well_separated_embedder(n, n_topics=4)
        result = run(cluster_reviews(reviews, embedder, tmp_path / "c.db", top_k=5))

        assert not result.fallback_used
        # HDBSCAN should find at least 2 distinct clusters from 4 obvious groups
        assert len(result.clusters) >= 2

    # P3-E2: second call on same corpus → all cache hits (api_calls = 0)
    def test_second_run_all_cache_hits(self, tmp_path: Path) -> None:
        reviews = make_reviews(20)
        preset = np.eye(20, 8, dtype=np.float32)
        embedder1 = FakeEmbedder(preset)
        run(cluster_reviews(reviews, embedder1, tmp_path / "c.db"))

        embedder2 = FakeEmbedder(preset)
        # Monkeypatch embed_batch to detect if it's called
        called = []

        async def _spy(texts):  # type: ignore[no-untyped-def]
            called.append(texts)
            return preset[: len(texts)]

        embedder2.embed_batch = _spy  # type: ignore[method-assign]
        run(cluster_reviews(reviews, embedder2, tmp_path / "c.db"))
        assert len(called) == 0  # cache served everything

    # P3-E3: tiny corpus → fallback fires, never crashes
    def test_tiny_corpus_triggers_fallback(self, tmp_path: Path) -> None:
        reviews = [
            make_clean_review(f"R{i}", rating=(i % 5) + 1) for i in range(8)
        ]
        preset = np.eye(8, 4, dtype=np.float32)
        embedder = FakeEmbedder(preset)
        result = run(cluster_reviews(reviews, embedder, tmp_path / "c.db"))
        assert result.fallback_used

    # P3-E4: single review after dedup → fallback
    def test_single_review_triggers_fallback(self, tmp_path: Path) -> None:
        reviews = [make_clean_review("only_one", rating=3)]
        preset = np.array([[1.0, 0.0]], dtype=np.float32)
        embedder = FakeEmbedder(preset)
        result = run(cluster_reviews(reviews, embedder, tmp_path / "c.db"))
        assert result.fallback_used

    def test_empty_input_returns_fallback(self, tmp_path: Path) -> None:
        embedder = FakeEmbedder(np.ones((1, 4), dtype=np.float32))
        result = run(cluster_reviews([], embedder, tmp_path / "c.db"))
        assert result.fallback_used
        assert result.clusters == []

    # P3-E6: top_k caps output
    def test_top_k_respected(self, tmp_path: Path) -> None:
        n = 80
        reviews = [
            make_clean_review(f"R{i}", rating=(i % 5) + 1) for i in range(n)
        ]
        embedder = _well_separated_embedder(n, n_topics=4)
        result = run(
            cluster_reviews(reviews, embedder, tmp_path / "c.db", top_k=2)
        )
        assert len(result.clusters) <= 2

    def test_noise_ids_are_disjoint_from_cluster_ids(self, tmp_path: Path) -> None:
        n = 80
        reviews = make_reviews(n)
        embedder = _well_separated_embedder(n)
        result = run(cluster_reviews(reviews, embedder, tmp_path / "c.db"))

        cluster_ids = {rid for c in result.clusters for rid in c.member_review_ids}
        noise_ids = set(result.noise_review_ids)
        assert cluster_ids.isdisjoint(noise_ids)
