"""Tests for the FastMCP Docs server tools."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from pulse.phase_6.mcp_servers.docs_server import mcp, reset


@pytest.fixture(autouse=True)
def clear_store():
    reset()
    yield
    reset()


def _parse(result) -> dict | list:
    # fastmcp 2.x: CallToolResult
    if hasattr(result, "content"):
        if result.content:
            return json.loads(result.content[0].text)
        if hasattr(result, "data"):
            return result.data
    if isinstance(result, list) and result:
        return json.loads(result[0].text)
    return json.loads(str(result))


class TestDocsGet:
    @pytest.mark.asyncio
    async def test_unknown_doc_returns_not_found(self):
        async with Client(mcp) as client:
            result = await client.call_tool("docs_get", {"doc_id": "nonexistent"})
        data = _parse(result)
        assert data["found"] is False
        assert data["anchors"] == []
        assert data["revision_id"] is None

    @pytest.mark.asyncio
    async def test_known_doc_returns_revision_and_anchors(self):
        async with Client(mcp) as client:
            await client.call_tool(
                "docs_batch_update",
                {"doc_id": "doc1", "anchor": "pulse-groww-2026-W17", "blocks": [{"type": "heading_2", "text": "Week", "anchor": "pulse-groww-2026-W17"}]},
            )
            result = await client.call_tool("docs_get", {"doc_id": "doc1"})
        data = _parse(result)
        assert data["found"] is True
        assert "pulse-groww-2026-W17" in data["anchors"]
        assert data["revision_id"] is not None


class TestDocsBatchUpdate:
    @pytest.mark.asyncio
    async def test_append_creates_section_and_returns_revision(self):
        blocks = [
            {"type": "heading_2", "text": "Week of 2026-04-14 (ISO 2026-W16) — 100 reviews", "anchor": "pulse-groww-2026-W16"},
            {"type": "heading_3", "text": "App crashes"},
            {"type": "paragraph", "text": "Users report frequent crashes."},
        ]
        async with Client(mcp) as client:
            result = await client.call_tool(
                "docs_batch_update",
                {"doc_id": "doc1", "anchor": "pulse-groww-2026-W16", "blocks": blocks},
            )
        data = _parse(result)
        assert "documentRevisionId" in data
        assert data["documentRevisionId"].startswith("rev_")

    @pytest.mark.asyncio
    async def test_anchor_recorded_after_update(self):
        async with Client(mcp) as client:
            await client.call_tool(
                "docs_batch_update",
                {"doc_id": "doc2", "anchor": "pulse-groww-2026-W17", "blocks": [{"type": "heading_2", "text": "x"}]},
            )
            get_result = await client.call_tool("docs_get", {"doc_id": "doc2"})
        data = _parse(get_result)
        assert "pulse-groww-2026-W17" in data["anchors"]

    @pytest.mark.asyncio
    async def test_each_update_produces_new_revision(self):
        async with Client(mcp) as client:
            r1 = await client.call_tool(
                "docs_batch_update",
                {"doc_id": "doc3", "anchor": "pulse-groww-2026-W16", "blocks": [{"type": "heading_2", "text": "W16"}]},
            )
            r2 = await client.call_tool(
                "docs_batch_update",
                {"doc_id": "doc3", "anchor": "pulse-groww-2026-W17", "blocks": [{"type": "heading_2", "text": "W17"}]},
            )
        assert _parse(r1)["documentRevisionId"] != _parse(r2)["documentRevisionId"]

    @pytest.mark.asyncio
    async def test_empty_blocks_raises(self):
        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "docs_batch_update",
                    {"doc_id": "doc4", "anchor": "pulse-groww-2026-W16", "blocks": []},
                )
