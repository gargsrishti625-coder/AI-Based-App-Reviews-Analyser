"""Tests for normalize.py and util/text.py."""
from __future__ import annotations

import pytest

from pulse.phase_2.preprocess.normalize import merge_title_body, normalize_text
from pulse.util.text import normalize_for_match, text_hash


class TestMergeTitleBody:
    # P2-E7
    def test_title_and_body_joined_with_newline(self) -> None:
        result = merge_title_body("Great App", "Works perfectly for my needs.")
        assert result == "Great App\nWorks perfectly for my needs."

    def test_none_title_uses_body_only(self) -> None:
        result = merge_title_body(None, "Works perfectly.")
        assert result == "Works perfectly."

    def test_empty_title_uses_body_only(self) -> None:
        result = merge_title_body("  ", "Works perfectly.")
        assert result == "Works perfectly."

    def test_both_parts_stripped(self) -> None:
        result = merge_title_body("  Title  ", "  Body  ")
        assert result == "Title\nBody"

    def test_empty_body_uses_title(self) -> None:
        result = merge_title_body("Good", "")
        assert result == "Good"

    def test_both_empty_returns_empty(self) -> None:
        result = merge_title_body(None, "")
        assert result == ""


class TestNormalizeText:
    def test_nfc_normalization(self) -> None:
        # NFD: 'é' as 'e' + combining acute accent
        nfd = "café"  # é as decomposed
        nfc = "café"   # é as composed
        assert normalize_text(nfd) == nfc

    def test_collapses_multiple_spaces(self) -> None:
        assert normalize_text("word1   word2\t\tword3") == "word1 word2 word3"

    def test_preserves_newlines(self) -> None:
        text = "line1\nline2"
        assert "\n" in normalize_text(text)

    def test_strips_zero_width_chars(self) -> None:
        text = "hel​lo"  # zero-width space
        assert normalize_text(text) == "hello"

    def test_trims_leading_trailing(self) -> None:
        assert normalize_text("  hello  ") == "hello"

    def test_preserves_emoji(self) -> None:
        text = "Great 😊 app"
        assert "😊" in normalize_text(text)

    def test_does_not_lowercase(self) -> None:
        assert normalize_text("UPPERCASE") == "UPPERCASE"

    def test_does_not_strip_punctuation(self) -> None:
        text = "Well done! Really good."
        result = normalize_text(text)
        assert "!" in result and "." in result


class TestTextHash:
    def test_stable_across_calls(self) -> None:
        assert text_hash("hello world") == text_hash("hello world")

    def test_different_text_differs(self) -> None:
        assert text_hash("hello") != text_hash("world")

    def test_whitespace_variants_same_hash(self) -> None:
        # Extra spaces collapse → same hash
        assert text_hash("hello  world") == text_hash("hello world")

    def test_nfc_nfd_same_hash(self) -> None:
        nfd = "café"
        nfc = "café"
        assert text_hash(nfd) == text_hash(nfc)

    def test_hex_string(self) -> None:
        h = text_hash("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_zero_width_normalized_before_hash(self) -> None:
        with_zwsp = "hel​lo"
        without = "hello"
        assert text_hash(with_zwsp) == text_hash(without)
