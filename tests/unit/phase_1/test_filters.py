"""Unit tests for review content filters in phase_1.ingestion.base."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulse.phase_1.ingestion.base import (
    FilterStats,
    RawReview,
    filter_reviews,
    has_emoji,
    in_window,
    is_non_english,
    is_too_short,
    synthetic_review_id,
)

_UTC = timezone.utc
_FETCHED_AT = datetime(2026, 4, 26, 12, 0, 0, tzinfo=_UTC)
_POSTED_IN_WINDOW = datetime(2026, 4, 15, 10, 0, 0, tzinfo=_UTC)
_WINDOW = (datetime(2026, 3, 1, 0, 0, 0, tzinfo=_UTC), datetime(2026, 4, 26, 23, 59, 59, tzinfo=_UTC))


def _make_review(
    body: str,
    review_id: str = "R1",
    title: str | None = None,
    posted_at: datetime = _POSTED_IN_WINDOW,
    rating: int = 4,
) -> RawReview:
    return RawReview(
        source="app_store",
        review_id=review_id,
        product="groww",
        rating=rating,
        title=title,
        body=body,
        author="Alice",
        locale="in",
        posted_at=posted_at,
        fetched_at=_FETCHED_AT,
        raw={},
    )


class TestHasEmoji:
    def test_detects_face_emoji(self) -> None:
        assert has_emoji("Great app 😊") is True

    def test_detects_flag_emoji(self) -> None:
        assert has_emoji("I love 🇮🇳") is True

    def test_detects_symbol_emoji(self) -> None:
        assert has_emoji("Rating: ⭐⭐⭐⭐⭐") is True

    def test_clean_text_passes(self) -> None:
        assert has_emoji("This is a great investment app.") is False

    def test_empty_string(self) -> None:
        assert has_emoji("") is False

    def test_only_punctuation(self) -> None:
        assert has_emoji("!!! ??? ...") is False

    def test_hindi_script_not_emoji(self) -> None:
        # Devanagari is not in emoji blocks
        assert has_emoji("बहुत अच्छा") is False


class TestIsNonEnglish:
    def test_english_kept(self) -> None:
        assert is_non_english("This is a great investment and trading app.") is False

    def test_spanish_filtered(self) -> None:
        assert is_non_english("Esta aplicación es excelente para invertir en acciones.") is True

    def test_hindi_filtered(self) -> None:
        assert is_non_english("यह निवेश के लिए एक अच्छा ऐप है।") is True

    def test_empty_string_kept(self) -> None:
        # Empty → undetectable → keep (False)
        assert is_non_english("") is False

    def test_very_short_text_kept(self) -> None:
        # Too short for langdetect → keep (False)
        assert is_non_english("ok") is False


class TestIsTooShort:
    def test_three_letters_filtered(self) -> None:
        assert is_too_short("abc") is True

    def test_four_letters_kept(self) -> None:
        assert is_too_short("abcd") is False

    def test_letters_in_mixed_text(self) -> None:
        # "a b c" → 3 letters → filtered
        assert is_too_short("a b c") is True

    def test_all_numbers_filtered(self) -> None:
        assert is_too_short("1234") is True

    def test_normal_review_kept(self) -> None:
        assert is_too_short("Great app!") is False

    def test_empty_filtered(self) -> None:
        assert is_too_short("") is True

    def test_custom_min_letters(self) -> None:
        assert is_too_short("abcdef", min_letters=7) is True
        assert is_too_short("abcdefg", min_letters=7) is False


class TestInWindow:
    def test_exactly_at_start(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=_UTC)
        end = datetime(2026, 4, 26, tzinfo=_UTC)
        assert in_window(start, start, end) is True

    def test_exactly_at_end(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=_UTC)
        end = datetime(2026, 4, 26, tzinfo=_UTC)
        assert in_window(end, start, end) is True

    def test_before_start_excluded(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=_UTC)
        end = datetime(2026, 4, 26, tzinfo=_UTC)
        before = datetime(2026, 2, 28, tzinfo=_UTC)
        assert in_window(before, start, end) is False

    def test_after_end_excluded(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=_UTC)
        end = datetime(2026, 4, 26, tzinfo=_UTC)
        after = datetime(2026, 4, 27, tzinfo=_UTC)
        assert in_window(after, start, end) is False

    def test_naive_dt_treated_as_utc(self) -> None:
        start = datetime(2026, 3, 1, tzinfo=_UTC)
        end = datetime(2026, 4, 26, tzinfo=_UTC)
        naive = datetime(2026, 4, 1)  # no tzinfo
        assert in_window(naive, start, end) is True


class TestSyntheticReviewId:
    def test_deterministic(self) -> None:
        dt = datetime(2026, 4, 15, tzinfo=_UTC)
        id1 = synthetic_review_id("Alice", dt, "Great app")
        id2 = synthetic_review_id("Alice", dt, "Great app")
        assert id1 == id2

    def test_different_bodies_differ(self) -> None:
        dt = datetime(2026, 4, 15, tzinfo=_UTC)
        id1 = synthetic_review_id("Alice", dt, "Great app")
        id2 = synthetic_review_id("Alice", dt, "Bad app")
        assert id1 != id2

    def test_prefixed_with_synth(self) -> None:
        dt = datetime(2026, 4, 15, tzinfo=_UTC)
        assert synthetic_review_id(None, dt, "x").startswith("synth_")


class TestFilterReviews:
    def test_emoji_review_dropped(self) -> None:
        r = _make_review("Love this app 😊")
        kept, stats = filter_reviews([r], _WINDOW)
        assert kept == []
        assert stats.emoji_dropped == 1

    def test_non_english_review_dropped(self) -> None:
        r = _make_review("Esta aplicación es excelente para invertir en acciones.")
        kept, stats = filter_reviews([r], _WINDOW)
        assert kept == []
        assert stats.non_english_dropped == 1

    def test_too_short_review_dropped(self) -> None:
        r = _make_review("ok")
        kept, stats = filter_reviews([r], _WINDOW)
        assert kept == []
        assert stats.too_short_dropped == 1

    def test_out_of_window_review_dropped(self) -> None:
        old = datetime(2025, 1, 1, tzinfo=_UTC)
        r = _make_review("This is a great application overall.", posted_at=old)
        kept, stats = filter_reviews([r], _WINDOW)
        assert kept == []
        assert stats.window_dropped == 1

    def test_dedup_by_review_id(self) -> None:
        r1 = _make_review("Great app for investments.", review_id="DUP")
        r2 = _make_review("Another great review here.", review_id="DUP")
        kept, stats = filter_reviews([r1, r2], _WINDOW)
        assert len(kept) == 1
        assert stats.dedup_dropped == 1

    def test_valid_review_kept(self) -> None:
        r = _make_review("This is a great investment and trading application.")
        kept, stats = filter_reviews([r], _WINDOW)
        assert len(kept) == 1
        assert stats.total_dropped == 0

    def test_mixed_batch(self) -> None:
        reviews = [
            _make_review("Great investment app!", review_id="GOOD"),
            _make_review("Excelente aplicación para invertir en el mercado.", review_id="SPANISH"),
            _make_review("ok", review_id="SHORT"),
            _make_review("Love it 🎉", review_id="EMOJI"),
        ]
        kept, stats = filter_reviews(reviews, _WINDOW)
        assert len(kept) == 1
        assert kept[0].review_id == "GOOD"
        assert stats.non_english_dropped == 1
        assert stats.too_short_dropped == 1
        assert stats.emoji_dropped == 1

    def test_emoji_in_title_dropped(self) -> None:
        r = _make_review("This is an alright application.", title="Amazing! 🚀")
        kept, stats = filter_reviews([r], _WINDOW)
        assert kept == []
        assert stats.emoji_dropped == 1
