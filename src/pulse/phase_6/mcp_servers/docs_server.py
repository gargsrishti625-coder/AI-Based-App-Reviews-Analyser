"""FastMCP Google Docs server.

Exposes two tools the agent uses:
  docs_get          — fetch a doc's current structure (anchors + revision id)
  docs_batch_update — append a new section (blocks) under a named anchor

Backend selection (via env var GOOGLE_DOCS_ENABLED):
  - "true"  → real Google Docs API (requires credentials, see google_docs_api.py)
  - unset / any other value → in-memory store (dev / test mode)

Run as a standalone server:
    python -m pulse.phase_6.mcp_servers.docs_server
"""
from __future__ import annotations

import os
import uuid

from fastmcp import FastMCP

mcp = FastMCP(
    "pulse-docs",
    instructions=(
        "Google Docs MCP server for Pulse. "
        "Provides docs_get and docs_batch_update tools."
    ),
)

def _real_docs_enabled() -> bool:
    """Check the env flag at call time so tests can monkey-patch it."""
    return os.environ.get("GOOGLE_DOCS_ENABLED", "").lower() == "true"

# ── In-memory backing store (used when GOOGLE_DOCS_ENABLED != true) ──────────
_store: dict[str, dict] = {}


def _ensure(doc_id: str) -> dict:
    if doc_id not in _store:
        _store[doc_id] = {
            "revision_id": f"rev_{uuid.uuid4().hex[:8]}",
            "anchors": [],
            "sections": [],
        }
    return _store[doc_id]


def reset() -> None:
    """Clear all stored docs. Call from test fixtures."""
    _store.clear()


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool(name="docs_get")
async def docs_get(doc_id: str) -> dict:
    """Return a doc's current revision_id and the list of section anchors present.

    Args:
        doc_id: Google Doc ID (from product registry pulse_doc_id).

    Returns:
        {revision_id: str, anchors: [str], found: bool}
        found=False when the doc does not exist yet (first run).
    """
    if _real_docs_enabled():
        from pulse.phase_6.mcp_servers.google_docs_api import get_doc_info

        return get_doc_info(doc_id)

    if doc_id not in _store:
        return {"revision_id": None, "anchors": [], "found": False}
    doc = _store[doc_id]
    return {
        "revision_id": doc["revision_id"],
        "anchors": doc["anchors"],
        "found": True,
    }


@mcp.tool(name="docs_batch_update")
async def docs_batch_update(doc_id: str, anchor: str, blocks: list[dict]) -> dict:
    """Append a new section to the Doc under the given anchor.

    This call is NOT idempotent by itself — callers must check docs_get for
    the anchor before calling this.

    Args:
        doc_id: Google Doc ID.
        anchor: Section heading id (e.g. 'pulse-groww-2026-W17').
        blocks: List of DocBlock dicts (type, text, anchor?, attribution?).

    Returns:
        {documentRevisionId: str}
    """
    if not blocks:
        raise ValueError("blocks must not be empty")

    if _real_docs_enabled():
        from pulse.phase_6.mcp_servers.google_docs_api import append_blocks

        rev = append_blocks(doc_id, anchor, blocks)
        return {"documentRevisionId": rev}

    doc = _ensure(doc_id)
    doc["sections"].append({"anchor": anchor, "blocks": blocks})
    if anchor not in doc["anchors"]:
        doc["anchors"].append(anchor)

    new_rev = f"rev_{uuid.uuid4().hex[:8]}"
    doc["revision_id"] = new_rev
    return {"documentRevisionId": new_rev}


if __name__ == "__main__":
    # Only when invoked as a process (not when imported by tests) do we
    # populate environment variables from .env.
    from dotenv import load_dotenv

    load_dotenv()
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
