"""Unit tests for PlayStoreIngester — evaluations P1-E2, E4, E6, E7, E8."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from pulse.phase_0.core.types import ProductRegistryEntry
from pulse.phase_1.ingestion.play_store import PlayStoreIngester

_UTC = timezone.utc
_WINDOW_START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=_UTC)
_WINDOW_END = datetime(2026, 4, 26, 23, 59, 59, tzinfo=_UTC)
_WINDOW = (_WINDOW_START, _WINDOW_END)


def _make_product(play_store_id: str | None = "com.nextbillion.groww") -> ProductRegistryEntry:
    return ProductRegistryEntry(
        slug="groww",
        display_name="Groww",
        app_store_id=None,
        play_store_id=play_store_id,
        pulse_doc_id="DOC1",
        email_recipients=["team@example.com"],
    )


def _gps_review(
    review_id: str,
    score: int,
    content: str,
    author: str = "User",
    days_ago: int = 10,
    version: str | None = "9.5.0",
) -> dict:
    return {
        "reviewId": review_id,
        "userName": author,
        "content": content,
        "score": score,
        "at": datetime(2026, 4, 16, tzinfo=_UTC) - timedelta(days=days_ago),
        "appVersion": version,
        "thumbsUpCount": 0,
        "replyContent": None,
        "repliedAt": None,
    }


class TestPlayStoreIngester:
    # P1-E2: Play Store empty, returns IngestResult with status=empty
    async def test_empty_play_store_returns_empty_status(self) -> None:
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=([], None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert result.status == "empty"
        assert result.reviews == []
        assert result.source == "play_store"

    async def test_returns_raw_reviews_in_window(self) -> None:
        raw_reviews = [
            _gps_review("R1", 5, "This is a great investment application.", "User1"),
            _gps_review("R2", 4, "Good app for trading and investing daily.", "User2"),
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert result.status == "ok"
        assert len(result.reviews) == 2
        assert all(r.source == "play_store" for r in result.reviews)

    # P1-E4: Outside-window reviews dropped
    async def test_window_filter_drops_outside(self) -> None:
        raw_reviews = [
            _gps_review("IN1", 4, "Excellent investment platform for beginners.", days_ago=10),  # in window
            _gps_review("OUT1", 3, "This was pretty good application last year.", days_ago=500),  # before window
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        ids = {r.review_id for r in result.reviews}
        assert "IN1" in ids
        assert "OUT1" not in ids

    # P1-E6: Dedup within source
    async def test_dedup_by_review_id(self) -> None:
        raw_reviews = [
            _gps_review("DUP", 5, "Great platform for investment and trading.", "User1"),
            _gps_review("DUP", 5, "Great platform for investment and trading.", "User1"),
            _gps_review("UNIQ", 4, "Very useful application for daily investing.", "User2"),
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        ids = [r.review_id for r in result.reviews]
        assert ids.count("DUP") == 1
        assert "UNIQ" in ids

    # P1-E7: Missing app_version → None
    async def test_missing_app_version_is_none(self) -> None:
        raw_reviews = [
            _gps_review("R1", 4, "This is a solid investment application.", version=None),
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert len(result.reviews) == 1
        assert result.reviews[0].app_version is None

    # P1-E8: Cap enforced
    async def test_cap_enforced(self) -> None:
        raw_reviews = [
            _gps_review(f"R{i}", 4, "Good investment application overall.", f"User{i}")
            for i in range(30)
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=5)

        assert len(result.reviews) <= 5
        assert result.capped is True

    async def test_no_play_store_id_returns_failed(self) -> None:
        product = _make_product(play_store_id=None)
        result = await PlayStoreIngester().fetch(product, _WINDOW, cap=500)
        assert result.status == "failed"
        assert result.error is not None

    async def test_source_error_returns_failed(self) -> None:
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            side_effect=ConnectionError("network error"),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert result.status == "failed"
        assert result.error is not None

    async def test_emoji_review_filtered(self) -> None:
        raw_reviews = [
            _gps_review("EMOJI", 5, "Love it 😍❤️🔥", "EmojiUser"),
            _gps_review("CLEAN", 4, "Very useful investment application overall.", "CleanUser"),
        ]
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=(raw_reviews, None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        ids = {r.review_id for r in result.reviews}
        assert "EMOJI" not in ids
        assert "CLEAN" in ids

    async def test_synthetic_id_generated_when_missing(self) -> None:
        raw = _gps_review("", 4, "Excellent investment platform overall.")
        raw["reviewId"] = ""  # explicitly empty
        with patch(
            "pulse.phase_1.ingestion.play_store.gps_reviews",
            return_value=([raw], None),
        ):
            result = await PlayStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert len(result.reviews) == 1
        assert result.reviews[0].review_id.startswith("synth_")
