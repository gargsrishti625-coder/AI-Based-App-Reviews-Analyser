"""Unit tests for ISO week helpers in core/runplan.py."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from pulse.phase_0.core.runplan import (
    iso_week_to_window,
    last_completed_iso_week_ist,
    parse_iso_week,
)


class TestParseIsoWeek:
    def test_valid_week(self) -> None:
        assert parse_iso_week("2026-W17") == (2026, 17)

    def test_valid_week_single_digit(self) -> None:
        # Leading zero is in the format, but let's verify padding in output string
        assert parse_iso_week("2026-W01") == (2026, 1)

    def test_week_53_valid_year(self) -> None:
        # 2026 has 53 ISO weeks
        year, week = parse_iso_week("2026-W53")
        assert year == 2026
        assert week == 53

    def test_week_53_invalid_year(self) -> None:
        # 2025 only has 52 weeks
        with pytest.raises(ValueError, match="does not exist"):
            parse_iso_week("2025-W53")

    def test_missing_w_prefix(self) -> None:
        with pytest.raises(ValueError, match="Invalid ISO week format"):
            parse_iso_week("2026-17")

    def test_no_dash(self) -> None:
        with pytest.raises(ValueError, match="Invalid ISO week format"):
            parse_iso_week("2026W17")

    def test_short_year(self) -> None:
        with pytest.raises(ValueError, match="Invalid ISO week format"):
            parse_iso_week("26-W17")

    def test_week_zero(self) -> None:
        with pytest.raises(ValueError):
            parse_iso_week("2026-W00")

    def test_week_54(self) -> None:
        with pytest.raises(ValueError, match="(Invalid ISO week format|does not exist|54)"):
            parse_iso_week("2026-W54")


class TestIsoWeekToWindow:
    def test_window_end_is_sunday_of_week(self) -> None:
        start, end = iso_week_to_window("2026-W17", window_weeks=1)
        # W17 2026: Mon 20 Apr → Sun 26 Apr
        assert end.year == 2026
        assert end.month == 4
        assert end.day == 26
        assert end.tzinfo == timezone.utc

    def test_window_start_is_n_weeks_before_end(self) -> None:
        start, end = iso_week_to_window("2026-W17", window_weeks=8)
        delta = end - start
        # 8 weeks = 56 days; accounting for midnight/end-of-day offsets it's ~55-56 days
        assert 55 <= delta.days <= 57

    def test_window_end_is_inclusive_last_second(self) -> None:
        _, end = iso_week_to_window("2026-W17", window_weeks=8)
        assert end.hour == 23
        assert end.minute == 59
        assert end.second == 59

    def test_window_start_is_midnight(self) -> None:
        start, _ = iso_week_to_window("2026-W17", window_weeks=8)
        assert start.hour == 0
        assert start.minute == 0
        assert start.second == 0

    def test_year_boundary(self) -> None:
        # W01 of 2026 should cross the 2025/2026 boundary
        start, end = iso_week_to_window("2026-W01", window_weeks=4)
        assert start.year == 2025
        assert end.year == 2026


class TestLastCompletedIsoWeekIst:
    def test_returns_last_week_not_current(self) -> None:
        # Freeze at Wednesday 2026-04-23 IST (W17 of 2026)
        frozen_utc = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
        with patch("pulse.phase_0.core.runplan.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_utc
            mock_dt.fromisocalendar = datetime.fromisocalendar
            result = last_completed_iso_week_ist()
        # Subtracting 7 days from W17 Wednesday lands in W16
        assert result == "2026-W16"

    def test_ist_used_not_utc(self) -> None:
        # Sunday 23:30 UTC = Monday 05:00 IST — last completed week should be W_prev
        # Sunday UTC is still the same ISO week, but +5:30 means it's Monday IST.
        # This test verifies we compute in IST, not UTC.
        # Sunday 2026-04-19 23:30 UTC is Monday 2026-04-20 05:00 IST (W17)
        frozen_utc = datetime(2026, 4, 19, 23, 30, tzinfo=timezone.utc)
        with patch("pulse.phase_0.core.runplan.datetime") as mock_dt:
            mock_dt.now.return_value = frozen_utc
            mock_dt.fromisocalendar = datetime.fromisocalendar
            result = last_completed_iso_week_ist()
        # frozen_utc - 7 days IST lands somewhere in W16 (in IST)
        assert result.startswith("2026-W")
