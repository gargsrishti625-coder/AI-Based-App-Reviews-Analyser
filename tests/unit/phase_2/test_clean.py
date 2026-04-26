"""Integration tests for the Phase 2 clean() orchestrator — P2-E1..E9."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulse.phase_1.ingestion.base import RawReview
from pulse.phase_2.preprocess import clean
from pulse.phase_2.core.types import CleanReview, CorpusStats

_UTC = timezone.utc
_FETCHED = datetime(2026, 4, 26, 12, 0, tzinfo=_UTC)
_POSTED = datetime(2026, 4, 15, 10, 0, tzinfo=_UTC)


def _raw(
    body: str,
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
        posted_at=_POSTED,
        app_version="9.5.0",
        fetched_at=_FETCHED,
        raw={},
    )


class TestClean:
    # P2-E1: Email scrubbed
    def test_email_scrubbed_from_body(self) -> None:
        r = _raw("Email me at user@example.com for issues and customer support assistance")
        cleaned, _ = clean([r])
        assert len(cleaned) == 1
        assert "[email]" in cleaned[0].text
        assert "user@example.com" not in cleaned[0].text

    # P2-E2: Phone scrubbed
    def test_phone_scrubbed_from_body(self) -> None:
        r = _raw("Call +91 98765 43210 anytime for any payment or account related support")
        cleaned, _ = clean([r])
        assert len(cleaned) == 1
        assert "[phone]" in cleaned[0].text
        assert "98765" not in cleaned[0].text

    # P2-E3: Account number scrubbed
    def test_account_number_scrubbed(self) -> None:
        r = _raw("My account number is 123456789012 please verify it for the transfer immediately")
        cleaned, _ = clean([r])
        assert len(cleaned) == 1
        assert "[account]" in cleaned[0].text
        assert "123456789012" not in cleaned[0].text

    # P2-E4: Whitespace dedup
    def test_whitespace_variant_deduped(self) -> None:
        # Double-space variant and single-space variant of the same review
        base = "Great application for investment and mutual fund trading purposes today"
        r1 = _raw(base.replace("and", " and"), review_id="A")  # double space before "and"
        r2 = _raw(base, review_id="B")
        cleaned, stats = clean([r1, r2])
        # Only one survives; dedup_count = 1
        assert len(cleaned) == 1
        assert stats.dedup_count == 1

    # P2-E5: Short review dropped
    def test_short_review_dropped(self) -> None:
        r = _raw("Nice app.", review_id="SHORT")
        _, stats = clean([r])
        assert stats.dropped_short == 1

    # P2-E6: Non-English review dropped
    def test_non_english_dropped(self) -> None:
        r = _raw("यह निवेश के लिए एक बहुत अच्छा ऐप है और मुझे यह पसंद है।", review_id="HI")
        _, stats = clean([r])
        assert stats.dropped_lang == 1

    # P2-E7: Title + body merged
    def test_title_body_merged(self) -> None:
        r = _raw(
            "Body content here for investment app review daily usage purposes",
            title="Great Title For This Investment Application",
        )
        cleaned, _ = clean([r])
        assert len(cleaned) == 1
        text = cleaned[0].text
        assert "Great Title" in text
        assert "Body content here" in text

    # P2-E8: Emoji-only review dropped
    def test_emoji_only_dropped(self) -> None:
        r = _raw("😊😍❤️🔥⭐👍💯🎉", review_id="EMOJI")
        _, stats = clean([r])
        assert stats.dropped_short == 1

    # P2-E9: corpus_stats reconciles
    def test_corpus_stats_reconciles(self) -> None:
        reviews = [
            _raw("Great investment and trading app for all users here.", review_id="GOOD"),
            _raw("Nice one.", review_id="SHORT"),
            _raw("यह निवेश ऐप बहुत अच्छा है मुझे बहुत पसंद है और उपयोगी है।", review_id="HINDI"),
            _raw("Great investment and trading app for all users here.", review_id="DUP"),
        ]
        _, stats = clean(reviews)
        # Should not raise AssertionError
        stats.assert_reconciles()

    def test_review_id_prefixed_with_source(self) -> None:
        r = _raw("This is a really great application for all investment and trading needs.", review_id="123")
        cleaned, _ = clean([r])
        assert cleaned[0].review_id == "app_store:123"

    def test_text_hash_populated(self) -> None:
        r = _raw("This is a really great application for all investment and trading needs.")
        cleaned, _ = clean([r])
        assert len(cleaned[0].text_hash) == 64

    def test_cross_source_dedup(self) -> None:
        # Same text from two different sources → only one survives
        same_body = "This is a great investment and trading application for daily use."
        r_app = _raw(same_body, review_id="R1", source="app_store")
        r_play = _raw(same_body, review_id="R2", source="play_store")
        cleaned, stats = clean([r_app, r_play])
        assert len(cleaned) == 1
        assert stats.dedup_count == 1

    def test_empty_input_returns_empty(self) -> None:
        cleaned, stats = clean([])
        assert cleaned == []
        assert stats.total_in == 0
        assert stats.total_out == 0

    def test_pii_scrub_then_too_short_counts_as_dropped_short(self) -> None:
        # A review that is entirely PII → after scrub, text is too short
        r = _raw("user@example.com", review_id="PII_ONLY")
        cleaned, stats = clean([r])
        assert len(cleaned) == 0
        assert stats.dropped_short == 1
        assert stats.dropped_pii == 0


class TestQuoteValidationParity:
    """The Phase 4 contract: normalize_for_match applied to a quote must be a
    substring of the CleanReview.text (which was also produced via the same fn)."""

    def test_quote_is_substring_of_clean_text(self) -> None:
        from pulse.util.text import normalize_for_match

        r = _raw(
            "This is a great investment app for beginners and advanced traders alike.",
            review_id="QV1",
        )
        cleaned, _ = clean([r])
        assert len(cleaned) == 1

        # Simulate Phase 4 extracting a quote substring
        quote = "great investment app for beginners"
        normalized_quote = normalize_for_match(quote)
        assert normalized_quote in cleaned[0].text
