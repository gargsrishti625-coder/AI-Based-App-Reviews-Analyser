from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """Return the current UTC time. Indirected so tests can freeze it."""
    return datetime.now(tz=timezone.utc)
