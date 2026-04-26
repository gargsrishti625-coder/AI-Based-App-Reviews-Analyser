"""Review filtering for Phase 2: length and language."""
from __future__ import annotations

import re

from langdetect import DetectorFactory, LangDetectException
from langdetect import detect as _detect_lang

DetectorFactory.seed = 0  # deterministic

# Minimum word count for reliable language detection (mirrors Phase 1 threshold).
_MIN_LANG_DETECT_WORDS = 7

# Minimum non-emoji tokens to consider a review substantive.
# Emoji-only tokens don't count (they have no letters or digits).
# 5 is the floor: "great app for mutual funds" has real signal; single-word
# ratings ("Good", "Nice") don't and are excluded by the 5-token threshold.
_MIN_TOKENS_DEFAULT = 5

# Match any letter or digit across all Unicode scripts (so Hindi/Arabic/etc.
# tokens count as substantive). Emoji lack \w matches, so they are excluded.
_WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)


def _count_substantive_tokens(text: str) -> int:
    """Count whitespace-separated tokens that contain at least one word character.

    Emoji-only tokens are excluded (no \\w match). Letters from any Unicode
    script (Latin, Devanagari, Arabic, etc.) count toward the total.
    """
    return sum(1 for tok in text.split() if _WORD_CHAR_RE.search(tok))


def is_too_short(text: str, min_tokens: int = _MIN_TOKENS_DEFAULT) -> bool:
    """Return True if the text has fewer than *min_tokens* substantive tokens.

    Emoji-only tokens (no letters or digits) are excluded from the count,
    so an all-emoji review always fails this check.
    Boundary: count == min_tokens → keep (>= comparison).
    """
    return _count_substantive_tokens(text) < min_tokens


def is_target_language(text: str, target: str = "en") -> bool:
    """Return True if text is in the target language or detection is inconclusive.

    Only drops on confirmed non-target — never drops when confidence is ambiguous
    or text is too short for reliable detection.
    """
    words = text.strip().split()
    if len(words) < _MIN_LANG_DETECT_WORDS:
        return True  # too short to detect reliably → keep
    try:
        detected = _detect_lang(text)
        return detected == target
    except LangDetectException:
        return True  # ambiguous → keep
    except Exception:
        return True  # any other error → keep
