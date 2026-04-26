"""Tests for the FastMCP Gmail server tools."""
from __future__ import annotations

import json

import pytest
from fastmcp import Client

from pulse.phase_6.mcp_servers.gmail_server import mcp, reset


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


_RECIPIENTS = ["alice@example.com"]
_SUBJECT = "Groww Pulse W17"
_HTML = "<p>Hello</p>"
_TEXT = "Hello"


class TestGmailMessagesSend:
    @pytest.mark.asyncio
    async def test_send_returns_message_id(self):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "gmail_messages_send",
                {
                    "to": _RECIPIENTS,
                    "subject": _SUBJECT,
                    "html_body": _HTML,
                    "text_body": _TEXT,
                },
            )
        data = _parse(result)
        assert "id" in data
        assert data["id"].startswith("msg_")

    @pytest.mark.asyncio
    async def test_send_each_call_produces_unique_id(self):
        async with Client(mcp) as client:
            r1 = await client.call_tool(
                "gmail_messages_send",
                {"to": _RECIPIENTS, "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
            )
            r2 = await client.call_tool(
                "gmail_messages_send",
                {"to": _RECIPIENTS, "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
            )
        assert _parse(r1)["id"] != _parse(r2)["id"]

    @pytest.mark.asyncio
    async def test_send_with_headers(self):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "gmail_messages_send",
                {
                    "to": _RECIPIENTS,
                    "subject": _SUBJECT,
                    "html_body": _HTML,
                    "text_body": _TEXT,
                    "headers": {"X-Pulse-Idempotency-Key": "abc123"},
                },
            )
        data = _parse(result)
        assert data["id"].startswith("msg_")

    @pytest.mark.asyncio
    async def test_send_empty_to_raises(self):
        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "gmail_messages_send",
                    {"to": [], "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
                )

    @pytest.mark.asyncio
    async def test_send_unsubstituted_deep_link_raises(self):
        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "gmail_messages_send",
                    {
                        "to": _RECIPIENTS,
                        "subject": _SUBJECT,
                        "html_body": "<p>{{PULSE_DEEP_LINK}}</p>",
                        "text_body": _TEXT,
                    },
                )


class TestGmailDraftsCreate:
    @pytest.mark.asyncio
    async def test_draft_returns_draft_id(self):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "gmail_drafts_create",
                {
                    "to": _RECIPIENTS,
                    "subject": _SUBJECT,
                    "html_body": _HTML,
                    "text_body": _TEXT,
                },
            )
        data = _parse(result)
        assert "id" in data
        assert data["id"].startswith("draft_")

    @pytest.mark.asyncio
    async def test_draft_each_call_produces_unique_id(self):
        async with Client(mcp) as client:
            r1 = await client.call_tool(
                "gmail_drafts_create",
                {"to": _RECIPIENTS, "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
            )
            r2 = await client.call_tool(
                "gmail_drafts_create",
                {"to": _RECIPIENTS, "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
            )
        assert _parse(r1)["id"] != _parse(r2)["id"]

    @pytest.mark.asyncio
    async def test_draft_empty_to_raises(self):
        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "gmail_drafts_create",
                    {"to": [], "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
                )

    @pytest.mark.asyncio
    async def test_draft_unsubstituted_deep_link_raises(self):
        async with Client(mcp) as client:
            with pytest.raises(Exception):
                await client.call_tool(
                    "gmail_drafts_create",
                    {
                        "to": _RECIPIENTS,
                        "subject": _SUBJECT,
                        "html_body": _HTML,
                        "text_body": "{{PULSE_DEEP_LINK}}",
                    },
                )


class TestGmailMessagesList:
    @pytest.mark.asyncio
    async def test_list_empty_when_no_messages_sent(self):
        async with Client(mcp) as client:
            result = await client.call_tool(
                "gmail_messages_list",
                {"query": "X-Pulse-Idempotency-Key:abc123"},
            )
        data = _parse(result)
        assert data == []

    @pytest.mark.asyncio
    async def test_list_finds_sent_message_by_idempotency_key(self):
        idem_key = "deadbeef1234"
        async with Client(mcp) as client:
            send_result = await client.call_tool(
                "gmail_messages_send",
                {
                    "to": _RECIPIENTS,
                    "subject": _SUBJECT,
                    "html_body": _HTML,
                    "text_body": _TEXT,
                    "headers": {"X-Pulse-Idempotency-Key": idem_key},
                },
            )
            sent_id = _parse(send_result)["id"]

            list_result = await client.call_tool(
                "gmail_messages_list",
                {"query": f"X-Pulse-Idempotency-Key:{idem_key}"},
            )
        data = _parse(list_result)
        assert len(data) == 1
        assert data[0]["id"] == sent_id

    @pytest.mark.asyncio
    async def test_list_does_not_find_draft_by_idempotency_key(self):
        idem_key = "draftkey5678"
        async with Client(mcp) as client:
            await client.call_tool(
                "gmail_drafts_create",
                {
                    "to": _RECIPIENTS,
                    "subject": _SUBJECT,
                    "html_body": _HTML,
                    "text_body": _TEXT,
                    "headers": {"X-Pulse-Idempotency-Key": idem_key},
                },
            )
            list_result = await client.call_tool(
                "gmail_messages_list",
                {"query": f"X-Pulse-Idempotency-Key:{idem_key}"},
            )
        data = _parse(list_result)
        assert data == []

    @pytest.mark.asyncio
    async def test_list_respects_limit(self):
        async with Client(mcp) as client:
            for _ in range(3):
                await client.call_tool(
                    "gmail_messages_send",
                    {"to": _RECIPIENTS, "subject": _SUBJECT, "html_body": _HTML, "text_body": _TEXT},
                )
            result = await client.call_tool(
                "gmail_messages_list",
                {"query": "subject:Groww", "limit": 2},
            )
        data = _parse(result)
        assert len(data) <= 2
