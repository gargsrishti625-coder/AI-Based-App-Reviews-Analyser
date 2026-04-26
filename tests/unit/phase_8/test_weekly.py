"""Phase 8 — weekly scheduler aggregation behavior."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from pulse.scheduler import weekly
from pulse.scheduler.pipeline import PipelineOutcome


@pytest.fixture
def fake_cfg() -> MagicMock:
    cfg = MagicMock()
    # Two products, deliberately out of alphabetical order in the dict
    cfg.products = {"zeta": MagicMock(), "alpha": MagicMock(), "mid": MagicMock()}
    return cfg


def _ok_plan() -> MagicMock:
    p = MagicMock()
    p.run_id = uuid4()
    return p


class TestWeeklyMain:
    def test_iterates_products_in_alphabetical_slug_order(
        self, fake_cfg: MagicMock, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        seen: list[str] = []

        def fake_bootstrap(*, config, product_slug, **kwargs):  # type: ignore[no-untyped-def]
            seen.append(product_slug)
            return _ok_plan()

        with patch.object(weekly, "load_config", return_value=fake_cfg), patch.object(
            weekly, "bootstrap", side_effect=fake_bootstrap
        ), patch.object(
            weekly,
            "execute_pipeline",
            return_value=PipelineOutcome.ok(uuid4()),
        ), patch.object(
            weekly, "last_completed_iso_week_ist", return_value="2026-W16"
        ):
            exit_code = weekly.main(["--config", "ignored.yaml"])

        assert exit_code == 0
        assert seen == ["alpha", "mid", "zeta"]

    def test_one_product_failure_doesnt_block_others_and_worst_exit_wins(
        self, fake_cfg: MagicMock, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        def fake_execute(plan, cfg, store):  # type: ignore[no-untyped-def]
            slug = plan.product.slug if hasattr(plan.product, "slug") else "x"
            if slug == "alpha":
                return PipelineOutcome.ok(uuid4())
            if slug == "mid":
                return PipelineOutcome.partial(uuid4(), "email_failed")
            return PipelineOutcome.failed(uuid4(), 4, "boom")

        # Make plan.product.slug match the product we're booting.
        def fake_bootstrap(*, config, product_slug, **kwargs):  # type: ignore[no-untyped-def]
            plan = _ok_plan()
            plan.product.slug = product_slug
            return plan

        with patch.object(weekly, "load_config", return_value=fake_cfg), patch.object(
            weekly, "bootstrap", side_effect=fake_bootstrap
        ), patch.object(
            weekly, "execute_pipeline", side_effect=fake_execute
        ), patch.object(
            weekly, "last_completed_iso_week_ist", return_value="2026-W16"
        ):
            exit_code = weekly.main([])

        # worst exit = failed (1) > partial (2)? No: partial=2, failed=1.
        # max(0, 2, 1) = 2.
        assert exit_code == 2

    def test_bootstrap_failure_per_product_recorded_as_failed(
        self, fake_cfg: MagicMock, tmp_path: Path, monkeypatch
    ) -> None:
        from pulse.phase_0.core.exceptions import PhaseFailure

        monkeypatch.chdir(tmp_path)
        fake_cfg.products = {"only": MagicMock()}

        with patch.object(weekly, "load_config", return_value=fake_cfg), patch.object(
            weekly,
            "bootstrap",
            side_effect=PhaseFailure(0, "config_missing_field"),
        ), patch.object(
            weekly, "last_completed_iso_week_ist", return_value="2026-W16"
        ):
            exit_code = weekly.main([])

        assert exit_code == 1

    def test_config_load_error_returns_64(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(
            weekly, "load_config", side_effect=FileNotFoundError("nope")
        ):
            exit_code = weekly.main([])
        assert exit_code == 64

    def test_explicit_week_override_used(
        self, fake_cfg: MagicMock, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        fake_cfg.products = {"alpha": MagicMock()}
        seen_weeks: list[str] = []

        def fake_bootstrap(*, config, product_slug, iso_week, **kwargs):  # type: ignore[no-untyped-def]
            seen_weeks.append(iso_week)
            return _ok_plan()

        with patch.object(weekly, "load_config", return_value=fake_cfg), patch.object(
            weekly, "bootstrap", side_effect=fake_bootstrap
        ), patch.object(
            weekly, "execute_pipeline", return_value=PipelineOutcome.ok(uuid4())
        ):
            weekly.main(["--week", "2024-W42"])

        assert seen_weeks == ["2024-W42"]
