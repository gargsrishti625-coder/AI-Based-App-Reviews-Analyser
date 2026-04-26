"""Phase 8 — CLI validation guards."""
from __future__ import annotations

import pytest

from pulse.phase_0.cli.main import _validate_run_args


class TestValidateRunArgs:
    def test_no_week_no_force_resend_passes(self) -> None:
        # Default invocation is allowed; week defaults to last completed.
        _validate_run_args(week=None, force_resend=False)

    def test_force_resend_without_week_rejected(self) -> None:
        with pytest.raises(ValueError, match="--force-resend requires --week"):
            _validate_run_args(week=None, force_resend=True)

    def test_past_week_passes(self) -> None:
        _validate_run_args(week="2024-W10", force_resend=False)

    def test_past_week_with_force_resend_passes(self) -> None:
        _validate_run_args(week="2024-W10", force_resend=True)

    def test_future_week_rejected(self) -> None:
        with pytest.raises(ValueError, match="not yet completed"):
            _validate_run_args(week="2099-W01", force_resend=False)

    def test_invalid_week_format_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid --week"):
            _validate_run_args(week="not-a-week", force_resend=False)

    def test_invalid_week_number_rejected(self) -> None:
        # 2025 only has 52 ISO weeks; W53 doesn't exist.
        with pytest.raises(ValueError, match="Invalid --week"):
            _validate_run_args(week="2025-W53", force_resend=False)
