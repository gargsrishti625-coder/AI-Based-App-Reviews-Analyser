"""Deterministic section anchor for a (product, iso_week) pair.

The anchor format is a stable contract: changing it breaks Phase 6 idempotency
for weeks already appended to the Google Doc.  Format: pulse-{slug}-{iso_week}
"""
from __future__ import annotations

import re


def anchor_for(product: str, iso_week: str) -> str:
    """Return the stable Doc-section anchor for a product + ISO week.

    >>> anchor_for("groww", "2026-W17")
    'pulse-groww-2026-W17'
    >>> anchor_for("Groww Mutual Funds", "2026-W17")
    'pulse-groww-mutual-funds-2026-W17'
    """
    slug = re.sub(r"[^a-z0-9]+", "-", product.lower()).strip("-")
    return f"pulse-{slug}-{iso_week}"
