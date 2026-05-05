"""Tests for the DuckDuckGo MCP client."""

from __future__ import annotations

import pytest

from mcp.base import McpCallResult, McpError
from mcp.duckduckgo import DUCKDUCKGO_TOOL_SEARCH, DuckDuckGoMcpClient


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


def _ok(text: str = "search results") -> McpCallResult:
    return McpCallResult(text=text, is_error=False)


def _err(text: str = "error") -> McpCallResult:
    return McpCallResult(text=text, is_error=True)


async def test_search_web_calls_correct_tool() -> None:
    """search_web sends to the correct DuckDuckGo tool name."""
    fake = FakeMcpClient(_ok())
    client = DuckDuckGoMcpClient(fake)

    await client.search_web("python asyncio")

    assert len(fake.calls) == 1
    tool_name, _ = fake.calls[0]
    assert tool_name == DUCKDUCKGO_TOOL_SEARCH


async def test_search_web_sends_query_and_max_results() -> None:
    """search_web sends query and maxResults in the arguments."""
    fake = FakeMcpClient(_ok())
    client = DuckDuckGoMcpClient(fake)

    await client.search_web("httpx tutorial", max_results=5)

    _, arguments = fake.calls[0]
    assert arguments["query"] == "httpx tutorial"
    assert arguments["maxResults"] == 5


async def test_search_web_default_max_results() -> None:
    """search_web uses a sensible default for max_results when not specified."""
    fake = FakeMcpClient(_ok())
    client = DuckDuckGoMcpClient(fake)

    await client.search_web("python typing")

    _, arguments = fake.calls[0]
    assert isinstance(arguments["maxResults"], int)
    assert arguments["maxResults"] > 0


async def test_search_web_returns_result() -> None:
    """search_web returns the McpCallResult from the transport."""
    fake = FakeMcpClient(_ok("result 1\nresult 2"))
    client = DuckDuckGoMcpClient(fake)

    result = await client.search_web("query")

    assert result.text == "result 1\nresult 2"
    assert not result.is_error


async def test_search_web_raises_on_empty_query() -> None:
    """An empty query raises McpError before calling the transport."""
    fake = FakeMcpClient(_ok())
    client = DuckDuckGoMcpClient(fake)

    with pytest.raises(McpError, match="must not be empty"):
        await client.search_web("")
    assert not fake.calls


async def test_search_web_propagates_mcp_error() -> None:
    """Transport-level McpError is propagated to the caller."""
    fake = FakeMcpClient(McpError("connection refused"))
    client = DuckDuckGoMcpClient(fake)

    with pytest.raises(McpError, match="connection refused"):
        await client.search_web("test query")


async def test_search_web_returns_is_error_result() -> None:
    """A result with is_error=True is returned as-is (not raised)."""
    fake = FakeMcpClient(_err("rate limited"))
    client = DuckDuckGoMcpClient(fake)

    result = await client.search_web("something")

    assert result.is_error
    assert result.text == "rate limited"


async def test_aclose_delegates_to_transport() -> None:
    """aclose() closes the underlying transport client."""
    fake = FakeMcpClient(_ok())
    client = DuckDuckGoMcpClient(fake)

    await client.aclose()

    assert fake.closed
