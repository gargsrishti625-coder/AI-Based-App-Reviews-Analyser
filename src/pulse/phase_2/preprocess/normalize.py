"""Text normalization for Phase 2."""
from __future__ import annotations

from pulse.util.text import normalize_for_match


def merge_title_body(title: str | None, body: str) -> str:
    """Join title and body with a newline. Strips whitespace from each part."""
    parts: list[str] = []
    if title and title.strip():
        parts.append(title.strip())
    body_stripped = body.strip() if body else ""
    if body_stripped:
        parts.append(body_stripped)
    return "\n".join(parts)


def normalize_text(text: str) -> str:
    """Apply the canonical normalization contract (NFC + invisible char removal + whitespace collapse)."""
    return normalize_for_match(text)
