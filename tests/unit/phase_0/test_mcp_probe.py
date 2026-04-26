"""Unit tests for MCP probe — evaluations P0-E6 and P0-E7."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.mcp.client import McpProbeError, ToolDescriptor, list_tools, probe


class TestListTools:
    async def test_returns_tool_descriptors(self) -> None:
        mock_tool_a = MagicMock()
        mock_tool_a.name = "docs.get"
        mock_tool_a.description = "Get a doc"
        mock_tool_b = MagicMock()
        mock_tool_b.name = "docs.batchUpdate"
        mock_tool_b.description = "Batch update"

        mock_result = MagicMock()
        mock_result.tools = [mock_tool_a, mock_tool_b]

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_streams = (AsyncMock(), AsyncMock())
        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__ = AsyncMock(return_value=mock_streams)
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("pulse.phase_0.mcp.client.sse_client", return_value=mock_sse_cm),
            patch("pulse.phase_0.mcp.client.ClientSession", return_value=mock_session),
        ):
            tools = await list_tools("http://localhost:8080/sse", timeout=5.0)

        assert len(tools) == 2
        assert tools[0] == ToolDescriptor(name="docs.get", description="Get a doc")
        assert tools[1] == ToolDescriptor(name="docs.batchUpdate", description="Batch update")

    async def test_timeout_raises_mcp_probe_error(self) -> None:
        import asyncio as _asyncio
        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__ = AsyncMock(side_effect=_asyncio.TimeoutError())
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("pulse.phase_0.mcp.client.sse_client", return_value=mock_sse_cm),
            patch("pulse.phase_0.mcp.client.ClientSession"),
        ):
            with pytest.raises(McpProbeError, match="timed out"):
                await list_tools("http://localhost:8080/sse", timeout=0.001)

    async def test_connection_error_raises_mcp_probe_error(self) -> None:
        mock_sse_cm = AsyncMock()
        mock_sse_cm.__aenter__ = AsyncMock(
            side_effect=ConnectionRefusedError("Connection refused")
        )
        mock_sse_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("pulse.phase_0.mcp.client.sse_client", return_value=mock_sse_cm),
            patch("pulse.phase_0.mcp.client.ClientSession"),
        ):
            with pytest.raises(McpProbeError):
                await list_tools("http://localhost:8080/sse", timeout=5.0)


class TestProbe:
    def _make_tools(self, names: list[str]) -> list[ToolDescriptor]:
        return [ToolDescriptor(name=n, description=None) for n in names]

    async def test_probe_success_when_all_tools_present(self) -> None:
        # P0-E6: both servers healthy → no exception
        with patch("pulse.phase_0.mcp.client.list_tools") as mock_lt:
            mock_lt.side_effect = [
                self._make_tools(["docs.get", "docs.batchUpdate"]),
                self._make_tools(["gmail.messages.send", "gmail.drafts.create"]),
            ]
            await probe(
                docs_url="http://docs/sse",
                gmail_url="http://gmail/sse",
                required_docs_tools=["docs.get", "docs.batchUpdate"],
                required_gmail_tools=["gmail.messages.send", "gmail.drafts.create"],
                timeout=5.0,
            )  # no exception → pass

    async def test_probe_fails_when_tool_missing(self) -> None:
        # P0-E7: docs server missing batchUpdate
        with patch("pulse.phase_0.mcp.client.list_tools") as mock_lt:
            mock_lt.side_effect = [
                self._make_tools(["docs.get"]),  # missing docs.batchUpdate
                self._make_tools(["gmail.messages.send", "gmail.drafts.create"]),
            ]
            with pytest.raises(PhaseFailure) as exc_info:
                await probe(
                    docs_url="http://docs/sse",
                    gmail_url="http://gmail/sse",
                    required_docs_tools=["docs.get", "docs.batchUpdate"],
                    required_gmail_tools=["gmail.messages.send", "gmail.drafts.create"],
                    timeout=5.0,
                )
            assert exc_info.value.phase == 0
            assert "docs.batchUpdate" in exc_info.value.reason

    async def test_probe_fails_when_server_unreachable(self) -> None:
        with patch("pulse.phase_0.mcp.client.list_tools") as mock_lt:
            mock_lt.side_effect = McpProbeError("http://docs/sse", "timed out")
            with pytest.raises(PhaseFailure) as exc_info:
                await probe(
                    docs_url="http://docs/sse",
                    gmail_url="http://gmail/sse",
                    required_docs_tools=["docs.get"],
                    required_gmail_tools=["gmail.messages.send"],
                    timeout=5.0,
                )
            assert exc_info.value.phase == 0

    async def test_probe_accumulates_errors_from_both_servers(self) -> None:
        # Both servers fail — error message should mention both
        with patch("pulse.phase_0.mcp.client.list_tools") as mock_lt:
            mock_lt.side_effect = [
                McpProbeError("http://docs/sse", "refused"),
                McpProbeError("http://gmail/sse", "refused"),
            ]
            with pytest.raises(PhaseFailure) as exc_info:
                await probe(
                    docs_url="http://docs/sse",
                    gmail_url="http://gmail/sse",
                    required_docs_tools=["docs.get"],
                    required_gmail_tools=["gmail.messages.send"],
                    timeout=5.0,
                )
            # Both server labels should appear in the reason
            assert "docs" in exc_info.value.reason
            assert "gmail" in exc_info.value.reason
