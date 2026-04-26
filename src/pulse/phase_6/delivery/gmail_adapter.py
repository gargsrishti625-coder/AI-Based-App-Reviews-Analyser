"""Agent-side adapter for the Gmail MCP server."""
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastmcp import Client

log = structlog.get_logger()

_DEEP_LINK_SENTINEL = "{{PULSE_DEEP_LINK}}"


def idempotency_key(product_slug: str, iso_week: str, doc_revision_id: str) -> str:
    """sha256(product_slug | iso_week | doc_revision_id) — stable across re-runs."""
    raw = f"{product_slug}|{iso_week}|{doc_revision_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def inject_deep_link(body: str, link: str) -> str:
    """Replace the {{PULSE_DEEP_LINK}} sentinel with the real URL."""
    if _DEEP_LINK_SENTINEL not in body:
        raise ValueError("Deep-link sentinel not found in email body")
    return body.replace(_DEEP_LINK_SENTINEL, link)


def _text(result: Any) -> str:
    """Extract a JSON string from a fastmcp call_tool result.

    fastmcp 2.x: CallToolResult with .content (TextContent list) and .data
    (pre-parsed Python object).  For list-returning tools fastmcp may put
    the value only in .data with empty .content, so we fall back to
    json.dumps(.data) in that case.
    """
    if hasattr(result, "content"):
        content = result.content
        if content:
            item = content[0]
            if hasattr(item, "text"):
                return item.text
        # Empty content — serialize the structured .data field
        if hasattr(result, "data"):
            return json.dumps(result.data)
        return "null"
    # Legacy: list of TextContent
    if isinstance(result, list) and result:
        item = result[0]
        return item.text if hasattr(item, "text") else str(item)
    return str(result)


async def gmail_messages_list(client: Client, query: str, limit: int = 1) -> list[dict]:
    result = await client.call_tool(
        "gmail_messages_list", {"query": query, "limit": limit}
    )
    return json.loads(_text(result))


async def gmail_messages_send(
    client: Client,
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str],
) -> str:
    """Send email; returns Gmail message_id."""
    result = await client.call_tool(
        "gmail_messages_send",
        {
            "to": to,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
            "headers": headers,
        },
    )
    data = json.loads(_text(result))
    msg_id = data.get("id")
    if not msg_id:
        raise ValueError("gmail_messages_send returned no id")
    log.info("gmail_send_ok", message_id=msg_id)
    return msg_id


async def gmail_drafts_create(
    client: Client,
    *,
    to: list[str],
    subject: str,
    html_body: str,
    text_body: str,
    headers: dict[str, str],
) -> str:
    """Create draft; returns Gmail draft_id."""
    result = await client.call_tool(
        "gmail_drafts_create",
        {
            "to": to,
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body,
            "headers": headers,
        },
    )
    data = json.loads(_text(result))
    draft_id = data.get("id")
    if not draft_id:
        raise ValueError("gmail_drafts_create returned no id")
    log.info("gmail_draft_ok", draft_id=draft_id)
    return draft_id
