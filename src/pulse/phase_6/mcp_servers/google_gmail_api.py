"""Real Gmail API backend for gmail_server.

Used when GOOGLE_GMAIL_ENABLED=true.  Shares OAuth credentials with the
Docs server via google_auth.build_service.

Idempotency note: Gmail's search query (`q`) does not index custom
headers, so we cannot reliably fetch a prior message by
``X-Pulse-Idempotency-Key``. The SQLite audit store
(``audit/store.py::find_prior_send``) is authoritative for re-run
detection. The header is still attached to outgoing messages so it
shows up in "View original" for forensic inspection.
"""
from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any


def _build_mime(
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None,
) -> str:
    """Build a multipart/alternative MIME message and return base64url(raw)."""
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    # From defaults to the authenticated mailbox; Gmail rewrites it server-side.
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    for header_name, header_value in (headers or {}).items():
        # Custom headers (X-Pulse-Idempotency-Key, X-Pulse-Run-ID) ride along
        # for forensic auditability; they don't affect delivery.
        msg[header_name] = header_value

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw


def send_message(
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> str:
    """Send a Gmail message; return the Gmail message id."""
    from pulse.phase_6.mcp_servers.google_auth import build_service

    service = build_service("gmail", "v1")
    raw = _build_mime(
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        headers=headers,
    )
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return str(sent["id"])


def create_draft(
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> str:
    """Create a Gmail draft; return the Gmail draft id."""
    from pulse.phase_6.mcp_servers.google_auth import build_service

    service = build_service("gmail", "v1")
    raw = _build_mime(
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
        headers=headers,
    )
    draft = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    return str(draft["id"])


def list_messages_by_header(
    header_key: str, header_value: str, limit: int = 1
) -> list[dict[str, Any]]:
    """Return [] — Gmail can't query custom headers via search.

    Idempotency for Pulse is owned by the SQLite audit store; this stub
    exists so the dispatcher in ``gmail_server.py`` can route uniformly.
    """
    return []
