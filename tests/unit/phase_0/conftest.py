"""Shared fixtures for Phase 0 tests."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pulse.phase_0.core.types import McpEndpoints, ProductRegistryEntry, PulseConfig


@pytest.fixture()
def minimal_config() -> PulseConfig:
    """Minimal valid config with a single 'groww' product."""
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
def config_yaml_path(tmp_path: Path, minimal_config: PulseConfig) -> Path:
    """Write minimal_config to a temp YAML file and return the path."""
    data = minimal_config.model_dump(mode="json")
    # Pydantic serialises AnyUrl objects as strings — yaml is happy with that.
    p = tmp_path / "pulse.yaml"
    p.write_text(yaml.dump(data), encoding="utf-8")
    return p
