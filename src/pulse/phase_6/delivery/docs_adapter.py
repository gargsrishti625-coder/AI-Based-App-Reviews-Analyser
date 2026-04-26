"""Agent-side adapter for the Docs MCP server.

All calls go through a fastmcp.Client — the server can be in-process
(tests) or a remote SSE endpoint (production).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastmcp import Client
    from pulse.phase_5.types import DocBlock

log = structlog.get_logger()


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


async def docs_get(client: Client, doc_id: str) -> dict:
    """Fetch a doc's revision_id and existing anchors."""
    result = await client.call_tool("docs_get", {"doc_id": doc_id})
    return json.loads(_text(result))


async def docs_batch_update(
    client: Client,
    doc_id: str,
    anchor: str,
    blocks: list[DocBlock],
) -> str:
    """Append blocks under anchor; returns new documentRevisionId."""
    serialized = [b.model_dump() for b in blocks]
    result = await client.call_tool(
        "docs_batch_update",
        {"doc_id": doc_id, "anchor": anchor, "blocks": serialized},
    )
    data = json.loads(_text(result))
    rev = data.get("documentRevisionId")
    if not rev:
        raise ValueError("docs_batch_update returned no documentRevisionId")
    log.info("docs_batch_update_ok", doc_id=doc_id, anchor=anchor, revision_id=rev)
    return rev


def anchor_exists(doc_info: dict, anchor: str) -> bool:
    """Return True if the anchor is already in the doc."""
    return anchor in (doc_info.get("anchors") or [])


def deep_link(doc_id: str, anchor: str) -> str:
    return f"https://docs.google.com/document/d/{doc_id}/edit#heading={anchor}"
