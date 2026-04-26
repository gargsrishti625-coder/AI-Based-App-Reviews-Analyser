"""Shared text normalization — used by Phase 2 (clean) and Phase 4 (quote validator).

Both callers MUST use the same function so that quote validation in Phase 4 can
correctly do substring matching against the scrubbed CleanReview.text from Phase 2.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

# Zero-width and bidirectional control characters that are visually invisible
# but make identical-looking strings hash differently.
_INVISIBLE_RE = re.compile(
    "["
    "​-‏"   # zero-width space/non-joiner/joiner/LRM/RLM
    "  "    # line/paragraph separator
    "﻿"          # BOM / zero-width no-break space
    "­"          # soft hyphen
    "‪- "   # bidirectional formatting chars
    "]"
)

# Collapse runs of horizontal whitespace (spaces/tabs) but preserve newlines.
_HSPACE_RE = re.compile(r"[ \t]+")


def normalize_for_match(text: str) -> str:
    """Canonical text normalization shared by Phase 2 and Phase 4.

    Contract (both phases MUST agree):
    - NFC unicode normalization
    - Remove zero-width / bidi control chars
    - Collapse runs of spaces/tabs to one space; preserve newlines
    - Trim leading/trailing whitespace
    - Do NOT lowercase; do NOT strip punctuation; keep emoji
    """
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE_RE.sub("", text)
    text = _HSPACE_RE.sub(" ", text)
    return text.strip()


def text_hash(text: str) -> str:
    """Stable SHA-256 of the NFC-normalized text. Used as dedup key in Phase 2."""
    return hashlib.sha256(normalize_for_match(text).encode("utf-8")).hexdigest()
