"""Apple App Store review ingester via iTunes RSS JSON endpoint.

Fetches up to *cap* reviews published within *window* by paginating:
  https://itunes.apple.com/{locale}/rss/customerreviews/page={N}/id={app_id}/sortby=mostrecent/json

No Google API is used. Retries use exponential back-off via tenacity.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from dateutil import parser as dateutil_parser
from tenacity import retry, stop_after_attempt, wait_exponential

from pulse.phase_0.core.types import ProductRegistryEntry

from .base import (
    IngestResult,
    RawReview,
    filter_reviews,
    in_window,
    synthetic_review_id,
)

_RSS_URL = (
    "https://itunes.apple.com/{locale}/rss/customerreviews"
    "/page={page}/id={app_id}/sortby=mostrecent/json"
)
_MAX_PAGES = 10
_DEFAULT_LOCALE = "in"


def _parse_iso_date(date_str: str) -> datetime:
    dt = dateutil_parser.parse(date_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _is_review_entry(entry: dict) -> bool:
    """Discriminate user-review entries from the app-info entry on page 1."""
    return "im:author" in entry and "im:rating" in entry


def _parse_entry(
    entry: dict,
    product_slug: str,
    locale: str,
    fetched_at: datetime,
) -> RawReview | None:
    try:
        rating = int(entry["im:rating"]["label"])
        if not (1 <= rating <= 5):
            return None

        review_id = entry.get("id", {}).get("label", "")
        title_raw = entry.get("title", {}).get("label")
        body = entry.get("content", {}).get("label", "")
        author = entry.get("im:author", {}).get("im:name", {}).get("label")
        updated = entry.get("updated", {}).get("label", "")
        version = entry.get("im:version", {}).get("label")

        posted_at = _parse_iso_date(updated)

        if not review_id:
            review_id = synthetic_review_id(author, posted_at, body)
            entry = {**entry, "synthetic_id": True}

        return RawReview(
            source="app_store",
            review_id=review_id,
            product=product_slug,
            rating=rating,
            title=title_raw or None,
            body=body,
            author=author or None,
            locale=locale,
            posted_at=posted_at,
            app_version=version or None,
            fetched_at=fetched_at,
            raw=entry,
        )
    except Exception:
        return None


class AppStoreIngester:
    """Paginated iTunes RSS JSON client for App Store reviews."""

    def __init__(self, locale: str = _DEFAULT_LOCALE) -> None:
        self._locale = locale

    async def fetch(
        self,
        product: ProductRegistryEntry,
        window: tuple[datetime, datetime],
        cap: int,
    ) -> IngestResult:
        if not product.app_store_id:
            return IngestResult(
                source="app_store",
                status="failed",
                error="no app_store_id configured for this product",
            )

        all_reviews: list[RawReview] = []
        pages_fetched = 0
        capped = False
        fetched_at = datetime.now(tz=timezone.utc)

        async with httpx.AsyncClient(timeout=30.0) as client:
            for page in range(1, _MAX_PAGES + 1):
                url = _RSS_URL.format(
                    locale=self._locale,
                    page=page,
                    app_id=product.app_store_id,
                )
                try:
                    data = await self._get_page(client, url)
                except Exception as exc:
                    if pages_fetched == 0:
                        return IngestResult(
                            source="app_store",
                            status="failed",
                            error=str(exc),
                        )
                    # At least some pages succeeded — surface as partial ok
                    break

                entries = data.get("feed", {}).get("entry", [])
                if not entries:
                    break

                pages_fetched += 1
                review_entries = [e for e in entries if _is_review_entry(e)]

                if not review_entries:
                    break

                oldest_dt: datetime | None = None
                for entry in review_entries:
                    review = _parse_entry(entry, product.slug, self._locale, fetched_at)
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

                # Reviews are ordered most-recent-first; if the oldest entry on
                # this page is already before window_start, all further pages will
                # also be out of window.
                if oldest_dt and oldest_dt < window[0]:
                    break

        kept, stats = filter_reviews(all_reviews, window)
        status: str = "ok" if kept else "empty"

        return IngestResult(
            source="app_store",
            reviews=kept,
            pages_fetched=pages_fetched,
            retries=0,  # tenacity handles internally; exposed via logging
            capped=capped,
            status=status,  # type: ignore[arg-type]
            filter_stats=stats,
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def _get_page(self, client: httpx.AsyncClient, url: str) -> dict:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
