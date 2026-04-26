from __future__ import annotations


class PhaseFailure(Exception):
    """Raised when a pipeline phase cannot continue.

    Caught at the top-level CLI/scheduler, which writes the audit row and exits with the
    appropriate exit code.
    """

    def __init__(self, phase: int, reason: str) -> None:
        self.phase = phase
        self.reason = reason
        super().__init__(f"Phase {phase} failed: {reason}")
