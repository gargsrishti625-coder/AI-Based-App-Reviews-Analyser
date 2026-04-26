"""Tests for Phase 4 quote validator — P4-E1, E2, E3."""
from __future__ import annotations

import pytest

from pulse.phase_2.core.types import CleanReview
from pulse.phase_3.core.types import Cluster
from pulse.phase_4.core.types import Quote
from pulse.llm.validate import validate_quote
from datetime import datetime, timezone

_UTC = timezone.utc
_POSTED = datetime(2026, 4, 15, tzinfo=_UTC)


def _review(review_id: str, text: str, rating: int = 4) -> CleanReview:
    return CleanReview(
        review_id=review_id,
        source="play_store",
        product="groww",
        rating=rating,
        locale="in",
        posted_at=_POSTED,
        text=text,
        text_hash=f"hash_{review_id}",
    )


def _cluster(member_ids: list[str]) -> Cluster:
    return Cluster(
        cluster_id=0,
        member_review_ids=member_ids,
        size=len(member_ids),
        centroid_review_ids=member_ids[:1],
        avg_rating=4.0,
        rating_distribution={4: len(member_ids)},
    )


class TestValidateQuote:
    # P4-E1: exact substring passes
    def test_exact_verbatim_quote_passes(self) -> None:
        review = _review("R1", "The app crashes every time I open the portfolio screen.")
        cluster = _cluster(["R1"])
        q = Quote(text="crashes every time I open the portfolio screen", review_id="R1")
        assert validate_quote(q, cluster, {"R1": review}) is True

    # P4-E2: quote not a substring → rejected
    def test_hallucinated_quote_rejected(self) -> None:
        review = _review("R1", "Great app for investing in mutual funds.")
        cluster = _cluster(["R1"])
        q = Quote(text="crashes every time I try to login", review_id="R1")
        assert validate_quote(q, cluster, {"R1": review}) is False

    def test_review_id_from_different_cluster_rejected(self) -> None:
        review = _review("R2", "Excellent interface and fast performance.")
        cluster = _cluster(["R1"])  # R2 not a member
        q = Quote(text="Excellent interface", review_id="R2")
        assert validate_quote(q, cluster, {"R2": review}) is False

    def test_review_id_not_in_reviews_dict_rejected(self) -> None:
        cluster = _cluster(["R1"])
        q = Quote(text="some text", review_id="R1")
        assert validate_quote(q, cluster, {}) is False

    def test_empty_quote_text_rejected(self) -> None:
        review = _review("R1", "Good app for trading.")
        cluster = _cluster(["R1"])
        q = Quote(text="", review_id="R1")
        assert validate_quote(q, cluster, {"R1": review}) is False

    def test_whitespace_collapsed_before_comparison(self) -> None:
        review = _review("R1", "Best  app for  investing today.")
        cluster = _cluster(["R1"])
        # Extra spaces in quote should still match after normalization
        q = Quote(text="Best  app for  investing", review_id="R1")
        assert validate_quote(q, cluster, {"R1": review}) is True

    def test_html_entities_unescaped_before_comparison(self) -> None:
        review = _review("R1", "Price > expectations & quality < promised.")
        cluster = _cluster(["R1"])
        # LLM might return the escaped version from the prompt
        q = Quote(text="Price &gt; expectations &amp; quality", review_id="R1")
        assert validate_quote(q, cluster, {"R1": review}) is True

    # P4-E3: all quotes fail → theme should be dropped (validate_quote returns False for all)
    def test_all_quotes_failing_returns_false(self) -> None:
        review = _review("R1", "Great investment app for beginners.")
        cluster = _cluster(["R1"])
        bad_quotes = [
            Quote(text="invented phrase one", review_id="R1"),
            Quote(text="invented phrase two", review_id="R1"),
        ]
        assert all(not validate_quote(q, cluster, {"R1": review}) for q in bad_quotes)
