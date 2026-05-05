"""Tests for the MCP Tool Protocol adapters."""

from __future__ import annotations

from mcp.base import McpCallResult, McpError
from mcp.context7 import Context7McpClient
from mcp.duckduckgo import DuckDuckGoMcpClient
from mcp.tools import QueryDocsTool, ResolveLibraryIdTool, SearchWebTool, make_mcp_tools
from tools.base import ToolResult


class FakeMcpTransport:
    """Minimal McpClient test double."""

    def __init__(self, result: McpCallResult | Exception) -> None:
        self._result = result
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> McpCallResult:
        self.calls.append((tool_name, arguments))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def aclose(self) -> None:
        pass


def _ok(text: str = "ok text") -> McpCallResult:
    return McpCallResult(text=text, is_error=False)


def _err(text: str = "error text") -> McpCallResult:
    return McpCallResult(text=text, is_error=True)


# ──────────────────────────────────────────────────────────────────────────────
# QueryDocsTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_query_docs_tool_name_and_description() -> None:
    """QueryDocsTool has the expected name and a non-empty description."""
    transport = FakeMcpTransport(_ok())
    tool = QueryDocsTool(Context7McpClient(transport))

    assert tool.name == "query_docs"
    assert tool.description


async def test_query_docs_tool_success_returns_ok_result() -> None:
    """A successful MCP call produces ToolResult(ok=True, output=...)."""
    transport = FakeMcpTransport(_ok("full docs here"))
    tool = QueryDocsTool(Context7McpClient(transport))

    result = await tool.call(library="/httpx")

    assert isinstance(result, ToolResult)
    assert result.ok
    assert result.output == "full docs here"
    assert not result.error


async def test_query_docs_tool_passes_topic_and_tokens() -> None:
    """topic and tokens are forwarded to the Context7 client."""
    transport = FakeMcpTransport(_ok("topic docs"))
    tool = QueryDocsTool(Context7McpClient(transport))

    await tool.call(library="/httpx", topic="async", tokens=2000)

    assert len(transport.calls) == 1
    _, args = transport.calls[0]
    assert args["topic"] == "async"
    assert args["tokens"] == 2000


async def test_query_docs_tool_mcp_error_returns_not_ok() -> None:
    """A McpError from the transport returns ToolResult(ok=False)."""
    transport = FakeMcpTransport(McpError("server offline"))
    tool = QueryDocsTool(Context7McpClient(transport))

    result = await tool.call(library="/httpx")

    assert not result.ok
    assert "server offline" in result.error


async def test_query_docs_tool_is_error_result_returns_not_ok() -> None:
    """An is_error=True McpCallResult returns ToolResult(ok=False)."""
    transport = FakeMcpTransport(_err("library not found"))
    tool = QueryDocsTool(Context7McpClient(transport))

    result = await tool.call(library="/missing")

    assert not result.ok
    assert "library not found" in result.error


# ──────────────────────────────────────────────────────────────────────────────
# ResolveLibraryIdTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_resolve_library_id_tool_name_and_description() -> None:
    """ResolveLibraryIdTool has the expected name and a non-empty description."""
    transport = FakeMcpTransport(_ok())
    tool = ResolveLibraryIdTool(Context7McpClient(transport))

    assert tool.name == "resolve_library_id"
    assert tool.description


async def test_resolve_library_id_tool_success_returns_ok_result() -> None:
    """A successful resolve returns ToolResult(ok=True, output=library_id)."""
    transport = FakeMcpTransport(_ok("/uv"))
    tool = ResolveLibraryIdTool(Context7McpClient(transport))

    result = await tool.call(library="uv")

    assert result.ok
    assert result.output == "/uv"


async def test_resolve_library_id_tool_mcp_error_returns_not_ok() -> None:
    """A McpError returns ToolResult(ok=False) with the error message."""
    transport = FakeMcpTransport(McpError("not reachable"))
    tool = ResolveLibraryIdTool(Context7McpClient(transport))

    result = await tool.call(library="httpx")

    assert not result.ok
    assert "not reachable" in result.error


async def test_resolve_library_id_tool_is_error_result_returns_not_ok() -> None:
    """An is_error=True result returns ToolResult(ok=False)."""
    transport = FakeMcpTransport(_err("no match"))
    tool = ResolveLibraryIdTool(Context7McpClient(transport))

    result = await tool.call(library="unknown-lib")

    assert not result.ok


# ──────────────────────────────────────────────────────────────────────────────
# SearchWebTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_search_web_tool_name_and_description() -> None:
    """SearchWebTool has the expected name and a non-empty description."""
    transport = FakeMcpTransport(_ok())
    tool = SearchWebTool(DuckDuckGoMcpClient(transport))

    assert tool.name == "search_web"
    assert tool.description


async def test_search_web_tool_success_returns_ok_result() -> None:
    """A successful search returns ToolResult(ok=True, output=results)."""
    transport = FakeMcpTransport(_ok("result one\nresult two"))
    tool = SearchWebTool(DuckDuckGoMcpClient(transport))

    result = await tool.call(query="python async tutorial")

    assert result.ok
    assert result.output == "result one\nresult two"


async def test_search_web_tool_passes_max_results() -> None:
    """max_results is forwarded to the DuckDuckGo client."""
    transport = FakeMcpTransport(_ok("results"))
    tool = SearchWebTool(DuckDuckGoMcpClient(transport))

    await tool.call(query="test", max_results=5)

    _, args = transport.calls[0]
    assert args["maxResults"] == 5


async def test_search_web_tool_mcp_error_returns_not_ok() -> None:
    """A McpError returns ToolResult(ok=False)."""
    transport = FakeMcpTransport(McpError("rate limit"))
    tool = SearchWebTool(DuckDuckGoMcpClient(transport))

    result = await tool.call(query="test")

    assert not result.ok
    assert "rate limit" in result.error


async def test_search_web_tool_is_error_result_returns_not_ok() -> None:
    """An is_error=True result returns ToolResult(ok=False)."""
    transport = FakeMcpTransport(_err("no results"))
    tool = SearchWebTool(DuckDuckGoMcpClient(transport))

    result = await tool.call(query="very obscure query")

    assert not result.ok


# ──────────────────────────────────────────────────────────────────────────────
# make_mcp_tools
# ──────────────────────────────────────────────────────────────────────────────


async def test_make_mcp_tools_returns_three_distinct_tools() -> None:
    """make_mcp_tools returns a tuple of three tools with the expected names."""
    ctx_transport = FakeMcpTransport(_ok())
    ddg_transport = FakeMcpTransport(_ok())
    context7 = Context7McpClient(ctx_transport)
    duckduckgo = DuckDuckGoMcpClient(ddg_transport)

    tools = make_mcp_tools(context7, duckduckgo)

    assert len(tools) == 3
    names = {t.name for t in tools}
    assert names == {"query_docs", "resolve_library_id", "search_web"}
