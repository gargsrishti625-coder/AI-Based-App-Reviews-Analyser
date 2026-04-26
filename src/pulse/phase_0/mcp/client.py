from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client

from pulse.phase_0.core.exceptions import PhaseFailure
from pulse.phase_0.obs.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str | None


class McpProbeError(Exception):
    """Raised when a server fails the probe — distinct from PhaseFailure so callers can
    accumulate errors across both servers before raising PhaseFailure(0)."""

    def __init__(self, server: str, reason: str) -> None:
        self.server = server
        self.reason = reason
        super().__init__(f"MCP probe failed for '{server}': {reason}")


async def list_tools(url: str, timeout: float) -> list[ToolDescriptor]:
    """Connect to an MCP server via SSE transport, initialize, list tools, and disconnect.

    Raises McpProbeError on any connection or protocol error.
    """
    try:
        async with asyncio.timeout(timeout):
            async with sse_client(url=url) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    return [
                        ToolDescriptor(name=t.name, description=t.description)
                        for t in result.tools
                    ]
    except TimeoutError as exc:
        raise McpProbeError(url, f"timed out after {timeout}s") from exc
    except Exception as exc:
        raise McpProbeError(url, str(exc)) from exc


async def probe(
    *,
    docs_url: str,
    gmail_url: str,
    required_docs_tools: list[str],
    required_gmail_tools: list[str],
    timeout: float,
) -> None:
    """Verify both MCP servers are reachable and expose the required tool surfaces.

    Raises PhaseFailure(0) if any server is unreachable or a required tool is missing.
    Accumulates all errors across both servers before raising so the operator sees the
    full picture in one shot.
    """
    errors: list[str] = []

    for server_label, url, required_tools in (
        ("docs", docs_url, required_docs_tools),
        ("gmail", gmail_url, required_gmail_tools),
    ):
        log.info("mcp_probe_start", server=server_label, url=url)
        try:
            tools = await list_tools(url, timeout)
        except McpProbeError as exc:
            errors.append(str(exc))
            log.error("mcp_probe_unreachable", server=server_label, error=str(exc))
            continue

        available = {t.name for t in tools}
        missing = [t for t in required_tools if t not in available]
        if missing:
            msg = (
                f"MCP server '{server_label}' ({url}) is missing required tools: "
                f"{missing}. Available: {sorted(available)}"
            )
            errors.append(msg)
            log.error("mcp_probe_missing_tools", server=server_label, missing=missing)
        else:
            log.info(
                "mcp_probe_ok",
                server=server_label,
                tools_count=len(tools),
            )

    if errors:
        raise PhaseFailure(0, "mcp_probe_failed: " + "; ".join(errors))
