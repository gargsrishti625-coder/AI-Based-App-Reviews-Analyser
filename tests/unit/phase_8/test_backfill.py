"""Phase 8 — backfill week range expansion + multi-week aggregation."""
from __future__ import annotations

from pulse.phase_0.cli.main import _expand_week_range


class TestExpandWeekRange:
    def test_single_week_range(self) -> None:
        assert _expand_week_range(2026, 10, 2026, 10) == ["2026-W10"]

    def test_simple_range_within_year(self) -> None:
        weeks = _expand_week_range(2026, 10, 2026, 13)
        assert weeks == ["2026-W10", "2026-W11", "2026-W12", "2026-W13"]

    def test_year_boundary_range(self) -> None:
        # 2025 has 52 ISO weeks. Span Dec 2025 → Jan 2026.
        weeks = _expand_week_range(2025, 51, 2026, 2)
        assert weeks == ["2025-W51", "2025-W52", "2026-W01", "2026-W02"]

    def test_descending_range_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="ascending"):
            _expand_week_range(2026, 13, 2026, 10)

    def test_iso_week_zero_padded(self) -> None:
        weeks = _expand_week_range(2026, 1, 2026, 3)
        assert weeks == ["2026-W01", "2026-W02", "2026-W03"]
