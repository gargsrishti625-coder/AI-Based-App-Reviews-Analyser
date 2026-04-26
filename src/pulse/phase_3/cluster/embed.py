"""Batched embedding API calls + on-disk SQLite cache."""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

if TYPE_CHECKING:
    from pulse.phase_2.core.types import CleanReview

log = structlog.get_logger()

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS embedding_cache (
  model_version TEXT NOT NULL,
  text_hash     TEXT NOT NULL,
  vector        BLOB NOT NULL,
  dim           INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  PRIMARY KEY (model_version, text_hash)
);
"""


@runtime_checkable
class Embedder(Protocol):
    model_version: str
    dim: int

    async def embed_batch(self, texts: list[str]) -> np.ndarray: ...


class EmbeddingCache:
    """SQLite-backed cache keyed on (model_version, text_hash)."""

    def __init__(self, db_path: Path) -> None:
        self._db = db_path
        with self._connect() as conn:
            conn.execute(_CACHE_DDL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, model_version: str, text_hash: str) -> np.ndarray | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT vector, dim FROM embedding_cache "
                "WHERE model_version=? AND text_hash=?",
                (model_version, text_hash),
            ).fetchone()
        if row is None:
            return None
        blob, dim = row
        return np.frombuffer(blob, dtype=np.float32).copy().reshape(dim)

    def put_many(
        self,
        model_version: str,
        text_hashes: list[str],
        vectors: np.ndarray,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (model_version, h, v.astype(np.float32).tobytes(), int(v.shape[0]), now)
            for h, v in zip(text_hashes, vectors)
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache "
                "(model_version, text_hash, vector, dim, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )


class SentenceTransformerEmbedder:
    """Local embedder using sentence-transformers. No API key, no quota, no cost.

    Default model: all-MiniLM-L6-v2 (384 dims, ~80MB, excellent for clustering).
    Downloaded once and cached by the sentence-transformers library.
    """

    def __init__(self, model: str = "all-MiniLM-L6-v2") -> None:
        from sentence_transformers import SentenceTransformer  # lazy import

        self.model_version = model
        self._model = SentenceTransformer(model)
        self.dim = self._model.get_sentence_embedding_dimension()

    async def embed_batch(self, texts: list[str]) -> np.ndarray:
        # SentenceTransformer.encode is synchronous; run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(
            None, lambda: self._model.encode(texts, show_progress_bar=False)
        )
        return np.array(vecs, dtype=np.float32)


class OpenAIEmbedder:
    """Wraps the OpenAI embeddings endpoint with tenacity retry.

    Kept for environments where OpenAI is preferred (higher quota, prod parity).
    Requires OPENAI_API_KEY env var and sufficient account credits.
    """

    def __init__(self, model: str = "text-embedding-3-small") -> None:
        import openai  # lazy import

        self.model_version = model
        self.dim = 1536  # text-embedding-3-small output dimension
        self._client = openai.AsyncOpenAI()

    @retry(wait=wait_exponential(min=2, max=65), stop=stop_after_attempt(6))
    async def embed_batch(self, texts: list[str]) -> np.ndarray:
        response = await self._client.embeddings.create(
            model=self.model_version, input=texts
        )
        return np.array([item.embedding for item in response.data], dtype=np.float32)


async def embed_reviews(
    reviews: list[CleanReview],
    embedder: Embedder,
    cache: EmbeddingCache,
    batch_size: int = 16,
    inter_batch_sleep: float = 2.0,
) -> tuple[np.ndarray, dict[str, int]]:
    """Return (N, D) embedding matrix aligned to *reviews*, plus cache metrics.

    Cache-hit reviews are read from SQLite; misses are batched and sent to the
    embedder, then persisted.  The returned matrix row order matches *reviews*.
    """
    n = len(reviews)
    dim = embedder.dim
    result = np.zeros((n, dim), dtype=np.float32)

    miss_indices: list[int] = []
    miss_hashes: list[str] = []
    miss_texts: list[str] = []

    for i, review in enumerate(reviews):
        cached = cache.get(embedder.model_version, review.text_hash)
        if cached is not None:
            result[i] = cached
        else:
            miss_indices.append(i)
            miss_hashes.append(review.text_hash)
            miss_texts.append(review.text)

    api_calls = 0
    if miss_texts:
        new_vecs_parts: list[np.ndarray] = []
        offsets = list(range(0, len(miss_texts), batch_size))
        for i, start in enumerate(offsets):
            batch = miss_texts[start : start + batch_size]
            vecs = await embedder.embed_batch(batch)
            new_vecs_parts.append(vecs)
            api_calls += 1
            # Pause between batches to stay within OpenAI RPM limits.
            # Skip the sleep after the last batch — no next request to protect.
            if i < len(offsets) - 1:
                await asyncio.sleep(inter_batch_sleep)

        all_new = np.vstack(new_vecs_parts)
        for local_idx, (global_idx, vec) in enumerate(zip(miss_indices, all_new)):
            result[global_idx] = vec

        cache.put_many(embedder.model_version, miss_hashes, all_new)

    metrics: dict[str, int] = {
        "cache_hits": n - len(miss_indices),
        "cache_misses": len(miss_indices),
        "api_calls": api_calls,
    }
    log.info("embedding_done", **metrics, model=embedder.model_version)
    return result, metrics
