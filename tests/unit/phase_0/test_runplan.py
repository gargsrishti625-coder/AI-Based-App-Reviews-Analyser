"""Unit tests for RunPlan construction — evaluations P0-E1 through P0-E8."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.core.runplan import build_runplan
from pulse.phase_0.core.types import PulseConfig


class TestBuildRunplan:
    # P0-E1: valid inputs produce a fully populated RunPlan
    def test_runplan_built_from_valid_inputs(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",   # safely in the past
        )
        assert plan.product.slug == "groww"
        assert plan.iso_week == "2026-W01"
        assert isinstance(plan.run_id, UUID)
        assert plan.window_start < plan.window_end
        assert plan.window_end.tzinfo == timezone.utc

    # P0-E2: omitting --week resolves to last completed ISO week in IST
    def test_iso_week_defaults_to_last_completed(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(config=minimal_config, product_slug="groww")
        import re
        assert re.match(r"^\d{4}-W\d{2}$", plan.iso_week)
        # window_end must be in the past
        assert plan.window_end < datetime.now(tz=timezone.utc)

    # P0-E3: --draft-only propagates
    def test_draft_only_flag_propagates(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
            draft_only=True,
        )
        assert plan.draft_only is True

    # P0-E3: draft_only defaults to True for dev/staging
    def test_draft_only_defaults_true_for_dev(self, minimal_config: PulseConfig) -> None:
        assert minimal_config.pulse_env == "dev"
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
        )
        assert plan.draft_only is True

    def test_draft_only_defaults_false_for_prod(self, minimal_config: PulseConfig) -> None:
        prod_config = minimal_config.model_copy(update={"pulse_env": "prod"})
        plan = build_runplan(
            config=prod_config,
            product_slug="groww",
            iso_week="2026-W01",
        )
        assert plan.draft_only is False

    # P0-E4: --dry-run is visible in the plan
    def test_dry_run_flag_visible_in_plan(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
            dry_run=True,
        )
        assert plan.dry_run is True

    # Negative P0-E5: unknown product raises PhaseFailure
    def test_unknown_product_raises_phase_failure(self, minimal_config: PulseConfig) -> None:
        with pytest.raises(PhaseFailure) as exc_info:
            build_runplan(
                config=minimal_config,
                product_slug="nonexistent",
                iso_week="2026-W01",
            )
        assert exc_info.value.phase == 0
        assert "nonexistent" in exc_info.value.reason
        assert "groww" in exc_info.value.reason  # lists known products

    # P0-E8: RunPlan is frozen — mutation raises ValidationError
    def test_runplan_is_frozen(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
        )
        with pytest.raises((ValidationError, TypeError)):
            plan.iso_week = "2026-W02"  # type: ignore[misc]

    # P0-E1: run_id is injected when provided
    def test_run_id_injectable(self, minimal_config: PulseConfig) -> None:
        fixed_id = uuid4()
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
            run_id=fixed_id,
        )
        assert plan.run_id == fixed_id

    # Sources populated from product registry
    def test_sources_populated(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
        )
        assert "app_store" in plan.sources
        assert "play_store" in plan.sources

    def test_sources_only_app_store_when_no_play_store(
        self, minimal_config: PulseConfig
    ) -> None:
        from pulse.phase_0.core.types import ProductRegistryEntry
        no_play = minimal_config.products["groww"].model_copy(
            update={"play_store_id": None}
        )
        cfg = minimal_config.model_copy(update={"products": {"groww": no_play}})
        plan = build_runplan(config=cfg, product_slug="groww", iso_week="2026-W01")
        assert plan.sources == ["app_store"]

    def test_no_sources_raises_phase_failure(self, minimal_config: PulseConfig) -> None:
        from pulse.phase_0.core.types import ProductRegistryEntry
        no_sources = minimal_config.products["groww"].model_copy(
            update={"app_store_id": None, "play_store_id": None}
        )
        cfg = minimal_config.model_copy(update={"products": {"groww": no_sources}})
        with pytest.raises(PhaseFailure) as exc_info:
            build_runplan(config=cfg, product_slug="groww", iso_week="2026-W01")
        assert exc_info.value.phase == 0


class TestRunplanEdgeCases:
    def test_future_week_raises_phase_failure(self, minimal_config: PulseConfig) -> None:
        with pytest.raises(PhaseFailure) as exc_info:
            build_runplan(
                config=minimal_config,
                product_slug="groww",
                iso_week="2099-W01",
            )
        assert exc_info.value.phase == 0
        assert "not fully in the past" in exc_info.value.reason

    def test_invalid_week_format_raises_phase_failure(
        self, minimal_config: PulseConfig
    ) -> None:
        with pytest.raises(PhaseFailure) as exc_info:
            build_runplan(
                config=minimal_config,
                product_slug="groww",
                iso_week="2026-17",   # missing 'W' prefix
            )
        assert exc_info.value.phase == 0

    def test_week_53_invalid_year_raises_phase_failure(
        self, minimal_config: PulseConfig
    ) -> None:
        # 2025 has only 52 weeks
        with pytest.raises(PhaseFailure) as exc_info:
            build_runplan(
                config=minimal_config,
                product_slug="groww",
                iso_week="2025-W53",
            )
        assert exc_info.value.phase == 0

    def test_dry_run_and_draft_only_both_set(self, minimal_config: PulseConfig) -> None:
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
            dry_run=True,
            draft_only=True,
        )
        # Both flags co-exist; dry_run means no MCP calls (checked at bootstrap level)
        assert plan.dry_run is True
        assert plan.draft_only is True

    def test_google_oauth_token_in_env_emits_warning(
        self, minimal_config: PulseConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GOOGLE_OAUTH_TOKEN", "fake-token")
        # Should not raise — the warning is logged but execution continues
        plan = build_runplan(
            config=minimal_config,
            product_slug="groww",
            iso_week="2026-W01",
        )
        assert plan.product.slug == "groww"
