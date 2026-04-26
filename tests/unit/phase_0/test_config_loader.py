"""Unit tests for config/loader.py."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pulse.phase_0.config.loader import load_config
from pulse.phase_0.core.types import PulseConfig


class TestLoadConfig:
    def test_valid_yaml_loads(self, config_yaml_path: Path) -> None:
        cfg = load_config(config_yaml_path)
        assert isinstance(cfg, PulseConfig)
        assert "groww" in cfg.products

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_malformed_yaml_raises_value_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("- item1\n- item2\n", encoding="utf-8")  # list, not mapping
        with pytest.raises(ValueError, match="must be a YAML mapping"):
            load_config(bad)

    def test_unknown_key_raises_validation_error(self, tmp_path: Path) -> None:
        p = tmp_path / "extra_key.yaml"
        data = {
            "pulse_env": "dev",
            "window_weeks": 8,
            "n_min_reviews": 20,
            "llm_model": "claude-sonnet-4-6",
            "embedding_model": "text-embedding-3-small",
            "total_token_cap": 200000,
            "max_reviews_per_source": 500,
            "mcp": {
                "docs_url": "http://localhost:8080/sse",
                "gmail_url": "http://localhost:8081/sse",
            },
            "products": {
                "groww": {
                    "slug": "groww",
                    "display_name": "Groww",
                    "pulse_doc_id": "DOC1",
                    "email_recipients": ["team@example.com"],
                }
            },
            "unexpected_field": "oops",  # extra key
        }
        p.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(p)

    def test_pulse_env_overridden_by_env_var(
        self, config_yaml_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PULSE_ENV", "prod")
        cfg = load_config(config_yaml_path)
        assert cfg.pulse_env == "prod"

    def test_missing_required_mcp_field(self, tmp_path: Path) -> None:
        p = tmp_path / "missing_mcp.yaml"
        data = {
            "pulse_env": "dev",
            "products": {
                "groww": {
                    "slug": "groww",
                    "display_name": "Groww",
                    "pulse_doc_id": "DOC1",
                    "email_recipients": ["team@example.com"],
                }
            },
            # mcp block entirely missing
        }
        p.write_text(yaml.dump(data), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(p)
