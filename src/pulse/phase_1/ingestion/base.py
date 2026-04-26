"""Base types, protocols, filters and shared helpers for Phase 1 review ingestion."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, stop_after_attempt, wait_exponential

from pulse.phase_0.core.types import ProductRegistryEntry

# ── emoji detection ──────────────────────────────────────────────────────────

_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U0001F1E0-\U0001F1FF"  # Flags
    "☀-⛿"           # Miscellaneous Symbols (☀ ★ ⛄ etc.)
    "✀-➿"           # Dingbats (✂ ✈ etc.)
    "⬀-⯿"           # Miscellaneous Symbols and Arrows (⭐ etc.)
    "]",
    flags=re.UNICODE,
)


def has_emoji(text: str) -> bool:
    """Return True if text contains any emoji character."""
    return bool(_EMOJI_RE.search(text))


# ── language detection ───────────────────────────────────────────────────────

try:
    from langdetect import DetectorFactory, LangDetectException
    from langdetect import detect as _detect_lang

    DetectorFactory.seed = 0  # make detection deterministic across runs
    _LANGDETECT_OK = True
except ImportError:  # pragma: no cover
    _LANGDETECT_OK = False


# langdetect misclassifies generic short phrases (e.g. "Excellent platform" → Norwegian).
# Seven words is the practical minimum for reliable Latin-script detection.
_MIN_LANG_DETECT_WORDS = 7


def is_non_english(text: str) -> bool:
    """Return True if text is detectably non-English.

    Returns False (keep the review) when detection is unavailable or the text
    has fewer than 7 words — too few words for confident Latin-script detection.
    Non-Latin scripts (Devanagari, Tamil, etc.) are typically detected correctly
    even at shorter lengths.
    """
    stripped = text.strip()
    if not _LANGDETECT_OK or len(stripped.split()) < _MIN_LANG_DETECT_WORDS:
        return False
    try:
        return _detect_lang(stripped) != "en"  # type: ignore[name-defined]
    except Exception:
        return False  # undetectable → keep


# ── length filter ────────────────────────────────────────────────────────────


def is_too_short(text: str, min_letters: int = 4) -> bool:
    """Return True if text contains fewer than *min_letters* alphabetic characters."""
    return sum(1 for c in text if c.isalpha()) < min_letters


# ── data models ──────────────────────────────────────────────────────────────


class FilterStats(BaseModel):
    emoji_dropped: int = 0
    non_english_dropped: int = 0
    too_short_dropped: int = 0
    window_dropped: int = 0
    dedup_dropped: int = 0

    @property
    def total_dropped(self) -> int:
        return (
            self.emoji_dropped
            + self.non_english_dropped
            + self.too_short_dropped
            + self.window_dropped
            + self.dedup_dropped
        )


class RawReview(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["app_store", "play_store"]
    review_id: str
    product: str
    rating: int = Field(ge=1, le=5)
    title: str | None = None
    body: str
    author: str | None = None
    locale: str | None = None
    posted_at: datetime  # tz-aware UTC
    app_version: str | None = None
    fetched_at: datetime  # tz-aware UTC
    raw: dict


class IngestResult(BaseModel):
    source: str
    reviews: list[RawReview] = Field(default_factory=list)
    pages_fetched: int = 0
    retries: int = 0
    capped: bool = False
    status: Literal["ok", "empty", "failed"]
    error: str | None = None
    filter_stats: FilterStats = Field(default_factory=FilterStats)


# ── protocol ─────────────────────────────────────────────────────────────────


class Ingester(Protocol):
    async def fetch(
        self,
        product: ProductRegistryEntry,
        window: tuple[datetime, datetime],
        cap: int,
    ) -> IngestResult: ...


# ── shared helpers ────────────────────────────────────────────────────────────


def in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    """Inclusive window check, UTC-normalised."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return start <= dt <= end


def synthetic_review_id(author: str | None, posted_at: datetime, body: str) -> str:
    """Deterministic surrogate ID from content fingerprint."""
    key = f"{author or ''}|{posted_at.isoformat()}|{body[:200]}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"synth_{digest}"


def filter_reviews(
    reviews: list[RawReview],
    window: tuple[datetime, datetime],
) -> tuple[list[RawReview], FilterStats]:
    """Apply all content and window filters. Returns (kept_reviews, stats)."""
    stats = FilterStats()
    seen_ids: set[str] = set()
    kept: list[RawReview] = []

    for r in reviews:
        # 1. Window filter
        if not in_window(r.posted_at, *window):
            stats.window_dropped += 1
            continue

        # 2. Deduplication
        if r.review_id in seen_ids:
            stats.dedup_dropped += 1
            continue
        seen_ids.add(r.review_id)

        combined_text = ((r.title or "") + " " + r.body).strip()

        # 3. Emoji filter (title + body)
        if has_emoji(combined_text):
            stats.emoji_dropped += 1
            continue

        # 4. Language filter (body only — titles are often short)
        if r.body.strip() and is_non_english(r.body):
            stats.non_english_dropped += 1
            continue

        # 5. Length filter (body alphabetic chars)
        if is_too_short(r.body):
            stats.too_short_dropped += 1
            continue

        kept.append(r)

    return kept, stats


# Shared tenacity decorator — apply with @_http_retry on async methods
_http_retry = retry(
    wait=wait_exponential(multiplier=1, min=1, max=8),
    stop=stop_after_attempt(4),
    reraise=True,
)
