"""Tests for the Context7 MCP client."""

from __future__ import annotations

import pytest

from mcp.base import McpCallResult, McpError
from mcp.context7 import CONTEXT7_TOOL_DOCS, CONTEXT7_TOOL_RESOLVE, Context7McpClient


class FakeMcpClient:
    """Test double that records calls and returns canned results."""

    def __init__(self, result: McpCallResult | Exception) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self._result = result
        self.closed = False

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> McpCallResult:
        self.calls.append((tool_name, arguments))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def aclose(self) -> None:
        self.closed = True


def _ok(text: str = "ok") -> McpCallResult:
    return McpCallResult(text=text, is_error=False)


def _err(text: str = "error") -> McpCallResult:
    return McpCallResult(text=text, is_error=True)


async def test_resolve_library_id_calls_correct_tool() -> None:
    """resolve_library_id sends to the correct Context7 tool name."""
    fake = FakeMcpClient(_ok("/uv"))
    client = Context7McpClient(fake)

    await client.resolve_library_id("uv")

    assert len(fake.calls) == 1
    tool_name, arguments = fake.calls[0]
    assert tool_name == CONTEXT7_TOOL_RESOLVE
    assert arguments == {"libraryName": "uv"}


async def test_resolve_library_id_returns_result() -> None:
    """resolve_library_id returns the McpCallResult from the transport."""
    fake = FakeMcpClient(_ok("/httpx"))
    client = Context7McpClient(fake)

    result = await client.resolve_library_id("httpx")

    assert result.text == "/httpx"
    assert not result.is_error


async def test_resolve_library_id_raises_on_empty_name() -> None:
    """An empty library_name raises McpError before calling the transport."""
    fake = FakeMcpClient(_ok())
    client = Context7McpClient(fake)

    with pytest.raises(McpError, match="must not be empty"):
        await client.resolve_library_id("")
    assert not fake.calls


async def test_resolve_library_id_propagates_mcp_error() -> None:
    """Transport-level McpError is propagated to the caller."""
    fake = FakeMcpClient(McpError("server down"))
    client = Context7McpClient(fake)

    with pytest.raises(McpError, match="server down"):
        await client.resolve_library_id("httpx")


async def test_query_docs_calls_correct_tool_without_topic() -> None:
    """query_docs without a topic sends context7CompatibleLibraryID only."""
    fake = FakeMcpClient(_ok("some docs"))
    client = Context7McpClient(fake)

    await client.query_docs("/uv")

    assert len(fake.calls) == 1
    tool_name, arguments = fake.calls[0]
    assert tool_name == CONTEXT7_TOOL_DOCS
    assert arguments["context7CompatibleLibraryID"] == "/uv"
    assert "topic" not in arguments


async def test_query_docs_includes_topic_when_provided() -> None:
    """query_docs with a topic includes it in the arguments."""
    fake = FakeMcpClient(_ok("async docs"))
    client = Context7McpClient(fake)

    await client.query_docs("/httpx", topic="async")

    _, arguments = fake.calls[0]
    assert arguments["topic"] == "async"


async def test_query_docs_sends_tokens_param() -> None:
    """query_docs includes the tokens parameter."""
    fake = FakeMcpClient(_ok("docs"))
    client = Context7McpClient(fake)

    await client.query_docs("/httpx", tokens=2000)

    _, arguments = fake.calls[0]
    assert arguments["tokens"] == 2000


async def test_query_docs_raises_on_empty_library_id() -> None:
    """An empty library_id raises McpError before calling the transport."""
    fake = FakeMcpClient(_ok())
    client = Context7McpClient(fake)

    with pytest.raises(McpError, match="must not be empty"):
        await client.query_docs("")
    assert not fake.calls


async def test_query_docs_returns_is_error_result() -> None:
    """A result with is_error=True is returned as-is (not raised)."""
    fake = FakeMcpClient(_err("library not found"))
    client = Context7McpClient(fake)

    result = await client.query_docs("/unknown")

    assert result.is_error
    assert result.text == "library not found"


async def test_aclose_delegates_to_transport() -> None:
    """aclose() closes the underlying transport client."""
    fake = FakeMcpClient(_ok())
    client = Context7McpClient(fake)

    await client.aclose()

    assert fake.closed
