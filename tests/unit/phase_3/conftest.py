"""Shared fixtures for Phase 3 unit tests."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from pulse.phase_2.core.types import CleanReview

_UTC = timezone.utc
_POSTED = datetime(2026, 4, 15, 10, 0, tzinfo=_UTC)


def make_clean_review(
    review_id: str,
    text: str = "placeholder text",
    rating: int = 4,
    text_hash: str | None = None,
) -> CleanReview:
    return CleanReview(
        review_id=review_id,
        source="app_store",
        product="groww",
        rating=rating,
        locale="in",
        posted_at=_POSTED,
        app_version="9.5.0",
        text=text,
        text_hash=text_hash or f"hash_{review_id}",
    )


def make_reviews(n: int, rating: int = 4) -> list[CleanReview]:
    return [make_clean_review(f"R{i}", rating=rating) for i in range(n)]


class FakeEmbedder:
    """Returns predetermined vectors; never calls an API."""

    def __init__(self, vectors: np.ndarray, model: str = "fake-model") -> None:
        self.model_version = model
        self.dim = vectors.shape[1]
        self._vectors = vectors
        self._call_count = 0

    async def embed_batch(self, texts: list[str]) -> np.ndarray:
        self._call_count += 1
        # Return the first len(texts) rows cycling through the preset vectors
        n = len(texts)
        indices = [i % len(self._vectors) for i in range(n)]
        return self._vectors[indices].astype(np.float32)
