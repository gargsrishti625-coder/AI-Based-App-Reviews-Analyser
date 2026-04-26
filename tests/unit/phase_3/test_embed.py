"""Tests for Phase 3 embedding + cache — P3-E2 and cache mechanics."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from pulse.phase_3.cluster.embed import EmbeddingCache, embed_reviews
from tests.unit.phase_3.conftest import FakeEmbedder, make_clean_review, make_reviews


class TestEmbeddingCache:
    def test_miss_returns_none(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "cache.db")
        assert cache.get("model-v1", "nonexistent") is None

    def test_put_then_get_roundtrips(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "cache.db")
        vec = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        cache.put_many("model-v1", ["h1"], np.array([vec]))
        result = cache.get("model-v1", "h1")
        assert result is not None
        np.testing.assert_array_almost_equal(result, vec)

    def test_different_model_versions_isolated(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "cache.db")
        v1 = np.array([1.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0], dtype=np.float32)
        cache.put_many("model-v1", ["h1"], np.array([v1]))
        cache.put_many("model-v2", ["h1"], np.array([v2]))
        np.testing.assert_array_almost_equal(cache.get("model-v1", "h1"), v1)
        np.testing.assert_array_almost_equal(cache.get("model-v2", "h1"), v2)

    def test_put_many_multiple_rows(self, tmp_path: Path) -> None:
        cache = EmbeddingCache(tmp_path / "cache.db")
        vecs = np.eye(4, dtype=np.float32)
        hashes = [f"h{i}" for i in range(4)]
        cache.put_many("m", hashes, vecs)
        for i, h in enumerate(hashes):
            result = cache.get("m", h)
            assert result is not None
            np.testing.assert_array_almost_equal(result, vecs[i])


class TestEmbedReviews:
    def _run(self, coro):  # type: ignore[no-untyped-def]
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_returns_correct_shape(self, tmp_path: Path) -> None:
        reviews = make_reviews(5)
        preset = np.random.default_rng(0).random((5, 8)).astype(np.float32)
        embedder = FakeEmbedder(preset)
        cache = EmbeddingCache(tmp_path / "c.db")
        X, metrics = self._run(embed_reviews(reviews, embedder, cache))
        assert X.shape == (5, 8)

    # P3-E2: second run has zero api_calls (all cache hits)
    def test_cache_hit_on_second_run(self, tmp_path: Path) -> None:
        reviews = make_reviews(4)
        preset = np.eye(4, dtype=np.float32)
        embedder = FakeEmbedder(preset)
        cache = EmbeddingCache(tmp_path / "c.db")

        # First call — all misses
        _, m1 = self._run(embed_reviews(reviews, embedder, cache))
        assert m1["cache_misses"] == 4
        assert m1["api_calls"] == 1

        # Second call — all hits
        embedder2 = FakeEmbedder(preset)
        _, m2 = self._run(embed_reviews(reviews, embedder2, cache))
        assert m2["cache_hits"] == 4
        assert m2["api_calls"] == 0

    def test_metrics_sum_to_n(self, tmp_path: Path) -> None:
        reviews = make_reviews(6)
        preset = np.ones((6, 4), dtype=np.float32)
        embedder = FakeEmbedder(preset)
        cache = EmbeddingCache(tmp_path / "c.db")
        _, metrics = self._run(embed_reviews(reviews, embedder, cache))
        assert metrics["cache_hits"] + metrics["cache_misses"] == 6

    def test_empty_reviews_returns_empty_matrix(self, tmp_path: Path) -> None:
        embedder = FakeEmbedder(np.ones((1, 4), dtype=np.float32))
        cache = EmbeddingCache(tmp_path / "c.db")
        X, metrics = self._run(embed_reviews([], embedder, cache))
        assert X.shape == (0, 4)
        assert metrics["api_calls"] == 0

    def test_partial_cache_hit(self, tmp_path: Path) -> None:
        reviews = make_reviews(4)
        preset = np.eye(4, dtype=np.float32)
        embedder = FakeEmbedder(preset)
        cache = EmbeddingCache(tmp_path / "c.db")

        # Pre-populate cache for first 2 reviews only
        cache.put_many(
            "fake-model",
            [r.text_hash for r in reviews[:2]],
            preset[:2],
        )

        _, metrics = self._run(embed_reviews(reviews, embedder, cache))
        assert metrics["cache_hits"] == 2
        assert metrics["cache_misses"] == 2
