"""Shared fixtures for Phase 2 tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulse.phase_1.ingestion.base import RawReview

_UTC = timezone.utc
_FETCHED_AT = datetime(2026, 4, 26, 12, 0, 0, tzinfo=_UTC)
_POSTED_AT = datetime(2026, 4, 15, 10, 0, 0, tzinfo=_UTC)


@pytest.fixture()
def make_raw_review():
    def _factory(
        body: str = "This is a great investment and trading application.",
        title: str | None = None,
        review_id: str = "R1",
        source: str = "app_store",
        rating: int = 4,
    ) -> RawReview:
        return RawReview(
            source=source,  # type: ignore[arg-type]
            review_id=review_id,
            product="groww",
            rating=rating,
            title=title,
            body=body,
            author="Alice",
            locale="in",
            posted_at=_POSTED_AT,
            app_version="9.5.0",
            fetched_at=_FETCHED_AT,
            raw={},
        )

    return _factory
