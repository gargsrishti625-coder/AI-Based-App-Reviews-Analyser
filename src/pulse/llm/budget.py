"""Token accounting with a hard per-run cap."""
from __future__ import annotations


class BudgetExceeded(Exception):
    """Raised when the estimated cost of the next call would exceed the cap."""


class Budget:
    """Mutable token counter shared across all LLM calls in one run."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._used = 0

    def add(self, tokens: int) -> None:
        self._used += tokens

    @property
    def used(self) -> int:
        return self._used

    @property
    def cap(self) -> int:
        return self._cap

    def remaining(self) -> int:
        return self._cap - self._used

    def check(self, estimated: int) -> None:
        """Raise BudgetExceeded proactively if remaining < estimated."""
        if self.remaining() < estimated:
            raise BudgetExceeded(
                f"Token budget exhausted: {self._used}/{self._cap} used; "
                f"need ≥{estimated} more."
            )
