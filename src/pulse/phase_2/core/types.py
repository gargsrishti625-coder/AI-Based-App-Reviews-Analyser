"""Phase 2 data models: CleanReview and CorpusStats."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CleanReview(BaseModel):
    review_id: str  # source-prefixed: "app_store:12345"
    source: str
    product: str
    rating: int
    locale: str | None = None
    posted_at: datetime
    app_version: str | None = None
    text: str           # normalized + PII-scrubbed
    text_hash: str      # sha256 of NFC + whitespace-collapsed text


class CorpusStats(BaseModel):
    total_in: int
    total_out: int
    dropped_pii: int = 0      # reviews dropped due to PII scrub making text empty/unusable
    dropped_short: int = 0    # reviews with too few tokens after normalization/scrub
    dropped_lang: int = 0     # reviews in non-target language
    dedup_count: int = 0      # exact-duplicate reviews removed by text_hash

    def assert_reconciles(self) -> None:
        """Arithmetic invariant: every input review must be accounted for exactly once."""
        accounted = (
            self.total_out
            + self.dropped_pii
            + self.dropped_short
            + self.dropped_lang
            + self.dedup_count
        )
        if self.total_in != accounted:
            raise AssertionError(
                f"corpus_stats does not reconcile: "
                f"total_in={self.total_in} != total_out({self.total_out}) "
                f"+ dropped_pii({self.dropped_pii}) "
                f"+ dropped_short({self.dropped_short}) "
                f"+ dropped_lang({self.dropped_lang}) "
                f"+ dedup_count({self.dedup_count}) = {accounted}"
            )
