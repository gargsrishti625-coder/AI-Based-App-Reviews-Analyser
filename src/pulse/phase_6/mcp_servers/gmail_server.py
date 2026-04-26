"""FastMCP Gmail server.

Exposes three tools the agent uses:
  gmail_messages_send   — send an email
  gmail_drafts_create   — save as draft
  gmail_messages_list   — list messages matching a Gmail search query

Backend selection (via env var GOOGLE_GMAIL_ENABLED):
  - "true"  → real Gmail API (shares OAuth credentials with the Docs server,
              see google_gmail_api.py)
  - unset / any other value → in-memory store (dev / test mode)

Run as a standalone server:
    python -m pulse.phase_6.mcp_servers.gmail_server
"""
from __future__ import annotations

import os
import uuid

from fastmcp import FastMCP

mcp = FastMCP(
    "pulse-gmail",
    instructions=(
        "Gmail MCP server for Pulse. "
        "Provides gmail_messages_send, gmail_drafts_create, and gmail_messages_list tools."
    ),
)


def _real_gmail_enabled() -> bool:
    """Check the env flag at call time so tests can monkey-patch it."""
    return os.environ.get("GOOGLE_GMAIL_ENABLED", "").lower() == "true"


# ── In-memory backing store (used when GOOGLE_GMAIL_ENABLED != true) ─────────
_sent: list[dict] = []    # [{id, to, subject, html_body, text_body, headers}]
_drafts: list[dict] = []  # same shape


def reset() -> None:
    """Clear in-memory state. Call from test fixtures."""
    _sent.clear()
    _drafts.clear()


def _validate_payload(
    to: list[str], html_body: str, text_body: str, action: str
) -> None:
    if not to:
        raise ValueError("to must not be empty")
    if "{{PULSE_DEEP_LINK}}" in html_body or "{{PULSE_DEEP_LINK}}" in text_body:
        raise ValueError(
            f"Deep-link placeholder was not substituted before {action}"
        )


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool(name="gmail_messages_send")
async def gmail_messages_send(
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> dict:
    """Send an email via Gmail.

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        html_body: HTML alternative body.
        text_body: Plain-text alternative body.
        headers: Extra headers dict (e.g. {'X-Pulse-Idempotency-Key': '...'}).

    Returns:
        {id: str}  — the Gmail message id.
    """
    _validate_payload(to, html_body, text_body, "send")

    if _real_gmail_enabled():
        from pulse.phase_6.mcp_servers.google_gmail_api import send_message

        message_id = send_message(
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        return {"id": message_id}

    msg = {
        "id": f"msg_{uuid.uuid4().hex[:12]}",
        "to": to,
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
        "headers": headers or {},
    }
    _sent.append(msg)
    return {"id": msg["id"]}


@mcp.tool(name="gmail_drafts_create")
async def gmail_drafts_create(
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str] | None = None,
) -> dict:
    """Save an email as a Gmail draft.

    Args:
        to: List of recipient email addresses.
        subject: Email subject line.
        html_body: HTML alternative body.
        text_body: Plain-text alternative body.
        headers: Extra headers dict.

    Returns:
        {id: str}  — the Gmail draft id.
    """
    _validate_payload(to, html_body, text_body, "draft")

    if _real_gmail_enabled():
        from pulse.phase_6.mcp_servers.google_gmail_api import create_draft

        draft_id = create_draft(
            to=to,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            headers=headers,
        )
        return {"id": draft_id}

    draft = {
        "id": f"draft_{uuid.uuid4().hex[:12]}",
        "to": to,
        "subject": subject,
        "html_body": html_body,
        "text_body": text_body,
        "headers": headers or {},
    }
    _drafts.append(draft)
    return {"id": draft["id"]}


@mcp.tool(name="gmail_messages_list")
async def gmail_messages_list(query: str, limit: int = 10) -> list[dict]:
    """List sent messages matching a Gmail search query.

    In-memory backend: searches the X-Pulse-Idempotency-Key header.
    Real-Gmail backend: returns []  — Gmail's `q` doesn't index custom
    headers, so Pulse relies on the SQLite audit store for idempotency.

    Args:
        query: Gmail search string (e.g. 'X-Pulse-Idempotency-Key:<key>').
        limit: Max results to return.

    Returns:
        List of {id, subject, headers} dicts.
    """
    if _real_gmail_enabled():
        from pulse.phase_6.mcp_servers.google_gmail_api import (
            list_messages_by_header,
        )

        # Parse "header:value" the same way the in-memory branch does.
        header_key, _, header_value = query.partition(":")
        return list_messages_by_header(
            header_key.strip(), header_value.strip(), limit=limit
        )

    results = []
    key_value: str | None = None
    if ":" in query:
        parts = query.split(":", 1)
        key_value = parts[1].strip() if len(parts) == 2 else None

    for msg in _sent:
        if key_value is None:
            results.append(
                {
                    "id": msg["id"],
                    "subject": msg["subject"],
                    "headers": msg["headers"],
                }
            )
        elif key_value in msg["headers"].values():
            results.append(
                {
                    "id": msg["id"],
                    "subject": msg["subject"],
                    "headers": msg["headers"],
                }
            )
        if len(results) >= limit:
            break

    return results


if __name__ == "__main__":
    # Only when invoked as a process (not when imported by tests) do we
    # populate environment variables from .env.
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run(transport="sse", host="0.0.0.0", port=8081)
