"""Shared Google OAuth/service-account credential factory.

Both the Docs MCP server and the Gmail MCP server authenticate with the
same Google identity using a single cached token (``GOOGLE_TOKEN_PATH``).
Pulling the auth logic into one place keeps the scope set unified, so a
single browser approval grants both APIs.

Backends, in priority order:

1. **Service account** — when ``GOOGLE_SERVICE_ACCOUNT_JSON`` is set
   (used in CI / scheduled GitHub Action). The doc and the destination
   Gmail mailbox must be shared with the service account.
2. **OAuth2 user** — for local dev. ``GOOGLE_CREDENTIALS_PATH`` points
   to a Desktop-app client secret JSON; first run opens a browser, the
   resulting token is cached at ``GOOGLE_TOKEN_PATH``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Union of every scope Pulse needs across all Google APIs. Listing them
# here (rather than per-API) means a single OAuth approval covers Docs +
# Gmail, and the cached token doesn't have to be recreated when a new API
# is added later.
_PULSE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/gmail.compose",
]


def build_service(api_name: str, api_version: str) -> Any:
    """Return an authenticated Google API client for *api_name*/*api_version*.

    Example:
        docs = build_service("docs", "v1")
        gmail = build_service("gmail", "v1")
    """
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        from google.oauth2 import service_account  # type: ignore[import-untyped]

        info = json.loads(sa_json)
        creds = service_account.Credentials.from_service_account_info(
            info,
            scopes=_PULSE_SCOPES,
        )
        return build(api_name, api_version, credentials=creds)

    # OAuth2 user credentials
    from google.auth.transport.requests import Request  # type: ignore[import-untyped]
    from google.oauth2.credentials import Credentials  # type: ignore[import-untyped]
    from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import-untyped]

    token_path = Path(os.environ.get("GOOGLE_TOKEN_PATH", "token.json"))
    creds_path = Path(
        os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    )

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(
            str(token_path), _PULSE_SCOPES
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"Google credentials not found at {creds_path}. "
                    "Download OAuth2 client credentials from Google Cloud "
                    "Console and set GOOGLE_CREDENTIALS_PATH in .env."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(creds_path), _PULSE_SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    return build(api_name, api_version, credentials=creds)
