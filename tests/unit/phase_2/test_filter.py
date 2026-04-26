"""Tests for length and language filters — evaluations P2-E5, E6, E8."""
from __future__ import annotations

import pytest

from pulse.phase_2.preprocess.filter import is_target_language, is_too_short


class TestIsTooShort:
    # P2-E5: 4-token review is dropped (below the 5-token threshold)
    def test_four_tokens_dropped(self) -> None:
        assert is_too_short("Good app I like") is True  # 4 tokens

    def test_five_tokens_kept(self) -> None:
        # exactly min_tokens=5 → keep (>= boundary)
        assert is_too_short("great app for mutual funds") is False  # 5 tokens

    def test_ten_tokens_kept(self) -> None:
        text = "This is a good application for investing and trading purposes"
        tokens = text.split()
        assert len(tokens) == 10
        assert is_too_short(text) is False

    def test_eleven_tokens_kept(self) -> None:
        text = "This is a really good application for all investing and trading purposes"
        assert is_too_short(text) is False

    def test_nine_tokens_kept(self) -> None:
        # 9 tokens is above the 5-token threshold
        text = "This is a good app for trading and investing"
        assert len(text.split()) == 9
        assert is_too_short(text) is False

    def test_four_tokens_still_dropped(self) -> None:
        # 4 tokens < 5 → still dropped
        assert is_too_short("Good app I like") is True

    # P2-E8: Emoji-only review → dropped (no substantive tokens)
    def test_emoji_only_dropped(self) -> None:
        assert is_too_short("😊😍❤️🔥⭐") is True

    def test_spaced_emoji_dropped(self) -> None:
        assert is_too_short("😊 😍 ❤️ 🔥 ⭐ 👍 💯 🎉") is True

    def test_only_url_placeholder_dropped(self) -> None:
        # "[url]" contains letters, counts as 1 token → below 10
        assert is_too_short("[url]") is True

    def test_empty_text_dropped(self) -> None:
        assert is_too_short("") is True

    def test_custom_min_tokens(self) -> None:
        assert is_too_short("hello world", min_tokens=3) is True
        assert is_too_short("hello world bye", min_tokens=3) is False

    def test_pii_placeholder_text_dropped(self) -> None:
        # Review that was entirely PII → "[email]" alone → 1 token
        assert is_too_short("[email]") is True

    def test_all_caps_counted(self) -> None:
        # All-caps should still count as substantive tokens
        text = "THIS IS A GREAT APP FOR INVESTMENT AND TRADING PURPOSES"
        assert is_too_short(text) is False


class TestIsTargetLanguage:
    def test_english_kept(self) -> None:
        text = "This is a great investment and trading application for all users."
        assert is_target_language(text) is True

    # P2-E6: Non-English review dropped
    def test_hindi_dropped(self) -> None:
        text = "यह निवेश के लिए एक बहुत अच्छा ऐप है।"
        assert is_target_language(text) is False

    def test_spanish_dropped(self) -> None:
        text = "Esta aplicación es excelente para invertir en acciones y bonos."
        assert is_target_language(text) is False

    def test_short_text_kept(self) -> None:
        # Too short → can't detect → keep
        assert is_target_language("ok") is True

    def test_empty_kept(self) -> None:
        assert is_target_language("") is True

    def test_six_words_kept(self) -> None:
        # Below 7-word threshold → skip detection → keep
        assert is_target_language("Excellent investment platform for beginners.") is True
