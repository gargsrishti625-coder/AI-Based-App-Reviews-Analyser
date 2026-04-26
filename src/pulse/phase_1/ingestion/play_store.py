"""Google Play Store review ingester via google-play-scraper.

Uses the `google-play-scraper` Python package which scrapes the Play Store
web interface.  This is NOT a Google API client and does not violate the
project's no-Google-SDK constraint.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
from google_play_scraper import Sort
from google_play_scraper import reviews as gps_reviews

from pulse.phase_0.core.types import ProductRegistryEntry

from .base import (
    IngestResult,
    RawReview,
    filter_reviews,
    synthetic_review_id,
)

_BATCH_SIZE = 200
_DEFAULT_LANG = "en"
_DEFAULT_COUNTRY = "in"


def _to_utc(dt: object) -> datetime:
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    parsed = dateutil_parser.parse(str(dt))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _map_review(
    raw: dict,
    product_slug: str,
    fetched_at: datetime,
) -> RawReview | None:
    try:
        review_id: str = raw.get("reviewId") or ""
        author: str | None = raw.get("userName")
        body: str = raw.get("content", "")
        rating: int = int(raw.get("score", 0))
        at = raw.get("at")
        version: str | None = raw.get("appVersion")

        if at is None:
            return None
        if not (1 <= rating <= 5):
            return None

        posted_at = _to_utc(at)

        payload = dict(raw)  # copy so we can annotate without mutating caller's dict
        if not review_id:
            review_id = synthetic_review_id(author, posted_at, body)
            payload["synthetic_id"] = True

        return RawReview(
            source="play_store",
            review_id=review_id,
            product=product_slug,
            rating=rating,
            title=None,  # Play Store reviews have no separate title field
            body=body,
            author=author,
            locale=_DEFAULT_COUNTRY,
            posted_at=posted_at,
            app_version=version or None,
            fetched_at=fetched_at,
            raw=payload,
        )
    except Exception:
        return None


class PlayStoreIngester:
    """google-play-scraper wrapper for Play Store reviews."""

    def __init__(
        self,
        lang: str = _DEFAULT_LANG,
        country: str = _DEFAULT_COUNTRY,
    ) -> None:
        self._lang = lang
        self._country = country

    async def fetch(
        self,
        product: ProductRegistryEntry,
        window: tuple[datetime, datetime],
        cap: int,
    ) -> IngestResult:
        if not product.play_store_id:
            return IngestResult(
                source="play_store",
                status="failed",
                error="no play_store_id configured for this product",
            )

        all_reviews: list[RawReview] = []
        token = None
        pages_fetched = 0
        capped = False
        fetched_at = datetime.now(tz=timezone.utc)

        while len(all_reviews) < cap:
            count = min(_BATCH_SIZE, cap - len(all_reviews))
            try:
                raw_batch, next_token = await asyncio.to_thread(
                    gps_reviews,
                    product.play_store_id,
                    lang=self._lang,
                    country=self._country,
                    sort=Sort.NEWEST,
                    count=count,
                    continuation_token=token,
                )
            except Exception as exc:
                if pages_fetched == 0:
                    return IngestResult(
                        source="play_store",
                        status="failed",
                        error=str(exc),
                    )
                break

            pages_fetched += 1
            if not raw_batch:
                break

            oldest_dt: datetime | None = None
            for raw in raw_batch:
                review = _map_review(raw, product.slug, fetched_at)
                if review is None:
                    continue
                all_reviews.append(review)
                if oldest_dt is None or review.posted_at < oldest_dt:
                    oldest_dt = review.posted_at
                if len(all_reviews) >= cap:
                    capped = True
                    break

            if capped:
                break

            # Stop paginating once we've gone past the window start
            if oldest_dt and oldest_dt < window[0]:
                break

            if next_token is None:
                break
            token = next_token

        kept, stats = filter_reviews(all_reviews, window)
        status: str = "ok" if kept else "empty"

        return IngestResult(
            source="play_store",
            reviews=kept,
            pages_fetched=pages_fetched,
            retries=0,
            capped=capped,
            status=status,  # type: ignore[arg-type]
            filter_stats=stats,
        )
