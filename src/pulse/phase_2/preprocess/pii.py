"""PII scrubbing for Phase 2.

Order of operations: URLs → emails → obfuscated emails → phones → accounts.
URLs are scrubbed first so that emails embedded in URLs don't survive scrubbing.
Account numbers (bare digit sequences) are scrubbed last so that formatted
phone numbers (with separators) are caught by the phone pattern first.

False-positive guards for account numbers:
- Skip if the digit run is part of a version string (preceded/followed by '.')
- Skip if the digit run is a 4-digit year in [1900, current+5]
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

# ── digit normalization ───────────────────────────────────────────────────────

# Arabic-Indic digits → ASCII
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def _normalize_digits(text: str) -> str:
    return text.translate(_ARABIC_INDIC)


# ── compiled PII patterns ─────────────────────────────────────────────────────

_URL = re.compile(r"https?://\S+", re.IGNORECASE)

_EMAIL = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)

_EMAIL_OBFUSCATED = re.compile(
    r"\b[A-Z0-9._%+\-]+"                              # local-part
    r"\s*(?:\(\s*at\s*\)|\[\s*at\s*\]|at)\s*"        # (at) / [at] / at
    r"[A-Z0-9.\-]+"                                    # domain
    r"\s*(?:\(\s*dot\s*\)|\[\s*dot\s*\]|dot)\s*"     # (dot) / [dot] / dot
    r"[A-Z]{2,}\b",                                    # TLD
    re.IGNORECASE,
)

# Phone: requires at least one non-digit separator in the number body, OR starts
# with international '+' prefix, OR is a sequence of spaced single digits.
_PHONE = re.compile(
    r"(?:"
    r"\+\d[\d\s\-\(\)\.]{7,}\d"                       # International: +CC rest
    r"|"
    r"\(?\d{3,5}\)?[\s\-\.]\d{3,5}(?:[\s\-\.]\d{2,})+"  # Formatted domestic
    r"|"
    r"(?:\d\s){5,}\d"                                  # Spaced digits: "9 8 7 6 5 4 3 2 1 0"
    r")"
)

# Bare consecutive digit sequences 10–16 chars — account / card numbers.
_ACCOUNT = re.compile(r"\b\d{10,16}\b")

_YEAR_RANGE = range(1900, datetime.now().year + 6)


def _is_account_false_positive(match: re.Match[str], text: str) -> bool:
    """Return True when the digit run should NOT be scrubbed."""
    start, end = match.span()
    matched = match.group()

    # Part of a version string: preceded or followed by '.'
    pre = text[max(0, start - 1) : start]
    post = text[end : end + 1]
    if pre == "." or post == ".":
        return True
    # Also preceded by 'v' (e.g. v1234567890)
    if start >= 1 and text[start - 1].lower() == "v":
        return True

    # 4-digit year
    if len(matched) == 4:
        try:
            if int(matched) in _YEAR_RANGE:
                return True
        except ValueError:
            pass

    return False


def scrub_pii(text: str) -> tuple[str, dict[str, int]]:
    """Scrub PII from *text*.

    Returns ``(scrubbed_text, counts)`` where ``counts`` is a dict with keys
    ``url``, ``email``, ``phone``, ``account`` and integer occurrence counts.
    """
    counts: dict[str, int] = {"url": 0, "email": 0, "phone": 0, "account": 0}

    # Normalize Arabic-Indic digits before matching
    text = _normalize_digits(text)

    # 1. URLs
    def _rep_url(m: re.Match[str]) -> str:
        counts["url"] += 1
        return "[url]"

    text = _URL.sub(_rep_url, text)

    # 2. Standard emails
    def _rep_email(m: re.Match[str]) -> str:
        counts["email"] += 1
        return "[email]"

    text = _EMAIL.sub(_rep_email, text)

    # 3. Obfuscated emails (user at example dot com)
    text = _EMAIL_OBFUSCATED.sub(_rep_email, text)

    # 4. Phone numbers (formatted / international)
    def _rep_phone(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group())
        if len(digits) >= 7:
            counts["phone"] += 1
            return "[phone]"
        return m.group()  # too few digits → not a phone

    text = _PHONE.sub(_rep_phone, text)

    # 5. Account/card numbers (bare digit sequences 10–16 digits)
    # Build a fresh closure over the current `text` so false-positive checks
    # inspect the same string the match offsets refer to.
    current_text = text

    def _rep_account(m: re.Match[str]) -> str:
        if _is_account_false_positive(m, current_text):
            return m.group()
        counts["account"] += 1
        return "[account]"

    text = _ACCOUNT.sub(_rep_account, text)

    return text, counts
