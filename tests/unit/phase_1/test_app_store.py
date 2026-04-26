"""Unit tests for AppStoreIngester — evaluations P1-E1, E4, E5, E6, E7, E8."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from pulse.phase_0.core.types import ProductRegistryEntry
from pulse.phase_1.ingestion.app_store import AppStoreIngester

_UTC = timezone.utc
_WINDOW_START = datetime(2026, 3, 1, 0, 0, 0, tzinfo=_UTC)
_WINDOW_END = datetime(2026, 4, 26, 23, 59, 59, tzinfo=_UTC)
_WINDOW = (_WINDOW_START, _WINDOW_END)


def _make_product(app_store_id: str | None = "1404379703") -> ProductRegistryEntry:
    return ProductRegistryEntry(
        slug="groww",
        display_name="Groww",
        app_store_id=app_store_id,
        play_store_id=None,
        pulse_doc_id="DOC1",
        email_recipients=["team@example.com"],
    )


def _rss_entry(
    review_id: str,
    rating: int,
    title: str,
    body: str,
    author: str,
    updated: str,
    version: str = "9.5.0",
) -> dict:
    return {
        "id": {"label": review_id},
        "im:rating": {"label": str(rating)},
        "title": {"label": title},
        "content": {"label": body, "attributes": {"type": "text"}},
        "im:author": {"uri": {"label": ""}, "im:name": {"label": author}},
        "updated": {"label": updated},
        "im:version": {"label": version},
    }


def _feed(entries: list[dict]) -> dict:
    return {"feed": {"entry": entries, "title": {"label": "Customer Reviews"}}}


def _in_window_date(offset_days: int = 0) -> str:
    dt = datetime(2026, 4, 15, tzinfo=_UTC) + timedelta(days=offset_days)
    return dt.isoformat()


def _before_window_date() -> str:
    return datetime(2025, 12, 1, tzinfo=_UTC).isoformat()


class TestAppStoreIngester:
    # P1-E1: 50 reviews in window → all yielded
    @respx.mock
    async def test_fetches_reviews_in_window(self) -> None:
        entries = [
            _rss_entry(f"R{i}", 4, f"Title {i}", "This app is great for investing.", f"User{i}", _in_window_date())
            for i in range(50)
        ]
        url_pattern = respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json")
        url_pattern.mock(return_value=httpx.Response(200, json=_feed(entries)))
        # Page 2 returns empty
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert result.status == "ok"
        assert result.source == "app_store"
        assert len(result.reviews) == 50
        assert all(r.source == "app_store" for r in result.reviews)

    # P1-E4: Reviews outside window are dropped
    @respx.mock
    async def test_window_filter_drops_outside(self) -> None:
        entries = [
            _rss_entry("IN1", 5, "Great", "This application is excellent for trading.", "User1", _in_window_date()),
            _rss_entry("OUT1", 3, "Old", "This was a good app back then.", "User2", _before_window_date()),
        ]
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed(entries))
        )
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        review_ids = {r.review_id for r in result.reviews}
        assert "IN1" in review_ids
        assert "OUT1" not in review_ids

    # P1-E5: 429 then 200 → eventual success
    @respx.mock
    async def test_429_retries_then_succeeds(self) -> None:
        entries = [
            _rss_entry("R1", 4, "Title", "This is a great investment application.", "User", _in_window_date()),
        ]
        page1_url = respx.get(
            "https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json"
        )
        # First call → 429, second → 200
        page1_url.side_effect = [
            httpx.Response(429, text="Too Many Requests"),
            httpx.Response(200, json=_feed(entries)),
        ]
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert result.status == "ok"
        assert len(result.reviews) >= 1

    # P1-E6: Duplicate review_id deduped
    @respx.mock
    async def test_dedup_by_review_id(self) -> None:
        entries = [
            _rss_entry("DUP_ID", 4, "Good", "Great application for investment needs.", "User", _in_window_date()),
            _rss_entry("DUP_ID", 4, "Good", "Great application for investment needs.", "User", _in_window_date()),
            _rss_entry("UNIQUE", 5, "Excellent", "Best investment platform available today.", "OtherUser", _in_window_date()),
        ]
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed(entries))
        )
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        ids = [r.review_id for r in result.reviews]
        assert ids.count("DUP_ID") == 1
        assert "UNIQUE" in ids

    # P1-E7: Missing app_version → None
    @respx.mock
    async def test_missing_app_version_is_none(self) -> None:
        entry = _rss_entry("R1", 4, "Good", "This is a solid investment application.", "User", _in_window_date())
        del entry["im:version"]  # missing version field
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([entry]))
        )
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert len(result.reviews) == 1
        assert result.reviews[0].app_version is None

    # P1-E8: Cap enforced
    @respx.mock
    async def test_cap_enforced(self) -> None:
        entries = [
            _rss_entry(f"R{i}", 4, "Good", "This is a great investment application.", f"User{i}", _in_window_date())
            for i in range(20)
        ]
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed(entries))
        )

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=5)

        assert len(result.reviews) <= 5
        assert result.capped is True

    async def test_no_app_store_id_returns_failed(self) -> None:
        product = _make_product(app_store_id=None)
        result = await AppStoreIngester().fetch(product, _WINDOW, cap=500)
        assert result.status == "failed"
        assert result.error is not None

    @respx.mock
    async def test_empty_feed_returns_empty(self) -> None:
        respx.get("https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json").mock(
            return_value=httpx.Response(200, json=_feed([]))
        )
        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)
        assert result.status == "empty"
        assert result.reviews == []

    @respx.mock
    async def test_stops_early_when_oldest_entry_before_window(self) -> None:
        page1 = [
            _rss_entry("NEW1", 5, "New", "This is excellent investment software.", "User", _in_window_date()),
            _rss_entry("OLD1", 3, "Old", "This was a good application.", "User", _before_window_date()),
        ]
        page1_mock = respx.get(
            "https://itunes.apple.com/in/rss/customerreviews/page=1/id=1404379703/sortby=mostrecent/json"
        )
        page1_mock.mock(return_value=httpx.Response(200, json=_feed(page1)))
        # Page 2 should NOT be requested since oldest on page 1 is before window
        page2_mock = respx.get(
            "https://itunes.apple.com/in/rss/customerreviews/page=2/id=1404379703/sortby=mostrecent/json"
        )
        page2_mock.mock(return_value=httpx.Response(200, json=_feed([])))

        result = await AppStoreIngester().fetch(_make_product(), _WINDOW, cap=500)

        assert page2_mock.call_count == 0
        assert any(r.review_id == "NEW1" for r in result.reviews)
