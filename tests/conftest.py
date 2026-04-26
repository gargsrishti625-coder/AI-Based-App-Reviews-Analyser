"""Project-wide test fixtures.

Keeps tests insulated from any environment leakage caused by .env loads
(the CLI's typer callback calls dotenv.load_dotenv on every invocation,
which would otherwise persist GOOGLE_DOCS_ENABLED across tests).
"""
from __future__ import annotations

import os

import pytest


_ISOLATED_ENV_VARS = (
    "GOOGLE_DOCS_ENABLED",
    "GOOGLE_GMAIL_ENABLED",
    "GOOGLE_CREDENTIALS_PATH",
    "GOOGLE_TOKEN_PATH",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
)


@pytest.fixture(autouse=True)
def _isolate_google_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip Google-specific env vars for every test so the in-memory
    docs server backend is selected even if .env was loaded somewhere
    upstream."""
    for var in _ISOLATED_ENV_VARS:
        if var in os.environ:
            monkeypatch.delenv(var, raising=False)
