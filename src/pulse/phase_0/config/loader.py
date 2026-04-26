from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from pulse.phase_0.core.types import PulseConfig

_ENV_OVERRIDE_KEY = "PULSE_ENV"
_DEFAULT_CONFIG_PATH = Path("config/pulse.yaml")
_ENV_VAR_RE = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


def _expand_env_vars(raw: dict) -> dict:
    """Replace '${VAR}' strings in product pulse_doc_id fields with env values."""
    products = raw.get("products", {})
    for slug, entry in products.items():
        if not isinstance(entry, dict):
            continue
        doc_id = entry.get("pulse_doc_id", "")
        m = _ENV_VAR_RE.match(str(doc_id))
        if m:
            var_name = m.group(1)
            value = os.environ.get(var_name)
            if not value:
                raise ValueError(
                    f"products.{slug}.pulse_doc_id references ${{{var_name}}} "
                    f"but that environment variable is not set."
                )
            entry["pulse_doc_id"] = value
    return raw


def load_config(path: Path | None = None) -> PulseConfig:
    """Load and validate configuration from a YAML file.

    Merges an optional ``PULSE_ENV`` env-var override on top of the file's
    ``pulse_env`` field so the operator can promote dev→staging→prod without
    editing the file.  Product ``pulse_doc_id`` values of the form
    ``${VAR_NAME}`` are expanded from environment variables so secrets do not
    need to be hardcoded in the YAML.

    Raises:
        FileNotFoundError: if the config file does not exist.
        ValueError: if YAML is not a mapping or a referenced env var is unset.
        pydantic.ValidationError: surfaced as-is so the CLI can pretty-print it.
    """
    resolved = path or _DEFAULT_CONFIG_PATH
    if not resolved.exists():
        raise FileNotFoundError(
            f"Config file not found: {resolved}. "
            "Pass --config or set PULSE_CONFIG to the correct path."
        )

    raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file {resolved} must be a YAML mapping at the top level."
        )

    # Allow PULSE_ENV env var to override the file value.
    if env := os.environ.get(_ENV_OVERRIDE_KEY):
        raw["pulse_env"] = env

    # Expand ${VAR} references in per-product pulse_doc_id fields.
    _expand_env_vars(raw)

    try:
        return PulseConfig.model_validate(raw)
    except ValidationError:
        raise  # let the caller (CLI) handle pretty-printing
