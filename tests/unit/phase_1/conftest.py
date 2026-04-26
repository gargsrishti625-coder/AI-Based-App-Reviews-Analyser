"""Shared fixtures for Phase 1 tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pulse.phase_0.core.types import McpEndpoints, ProductRegistryEntry, PulseConfig, RunPlan


@pytest.fixture()
def minimal_config() -> PulseConfig:
    return PulseConfig(
        pulse_env="dev",
        window_weeks=8,
        n_min_reviews=20,
        llm_model="claude-sonnet-4-6",
        embedding_model="text-embedding-3-small",
        total_token_cap=200_000,
        max_reviews_per_source=500,
        mcp=McpEndpoints(
            docs_url="http://localhost:8080/sse",  # type: ignore[arg-type]
            gmail_url="http://localhost:8081/sse",  # type: ignore[arg-type]
            probe_timeout_seconds=5.0,
        ),
        products={
            "groww": ProductRegistryEntry(
                slug="groww",
                display_name="Groww",
                app_store_id="1404379703",
                play_store_id="com.nextbillion.groww",
                pulse_doc_id="DOC_ID_TEST",
                email_recipients=["team@example.com"],
            )
        },
    )


@pytest.fixture()
def groww_product(minimal_config: PulseConfig) -> ProductRegistryEntry:
    return minimal_config.products["groww"]


@pytest.fixture()
def window_2026_w17() -> tuple[datetime, datetime]:
    """W17 2026: Mon 20 Apr – Sun 26 Apr, entire 8-week window ending Sun 26 Apr."""
    start = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 4, 26, 23, 59, 59, tzinfo=timezone.utc)
    return start, end
