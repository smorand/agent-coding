"""Tests for the McpClientFactory."""

from __future__ import annotations

import json

import httpx

from config_loader import McpConfig, McpEndpointConfig
from mcp.factory import McpClientFactory
from mcp.tools import QueryDocsTool, ResolveLibraryIdTool, SearchWebTool


def _mcp_config(
    context7_url: str = "http://ctx7.test",
    duckduckgo_url: str = "http://ddg.test",
) -> McpConfig:
    return McpConfig(
        context7=McpEndpointConfig(url=context7_url),
        duckduckgo=McpEndpointConfig(url=duckduckgo_url),
    )


def _tool_call_response(text: str = "result") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": False,
        },
    }


def _make_handler(text: str = "result", *, captured: dict[str, object] | None = None) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_tool_call_response(text))

    return handler


async def test_factory_from_config_builds_context7_client() -> None:
    """Factory builds a Context7 client targeting the configured URL."""
    captured: dict[str, object] = {}
    transport = httpx.MockTransport(_make_handler("ctx7 response", captured=captured))

    factory = McpClientFactory.from_config(_mcp_config(), transport=transport)
    tools = factory.build_tools()
    query_docs = next(t for t in tools if t.name == "query_docs")

    result = await query_docs.call(library="/httpx")

    assert captured.get("url") == "http://ctx7.test"
    assert isinstance(result, object)
    await factory.aclose()


async def test_factory_from_config_builds_duckduckgo_client() -> None:
    """Factory builds a DuckDuckGo client targeting the configured URL."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_tool_call_response("search results"))

    transport = httpx.MockTransport(handler)
    factory = McpClientFactory.from_config(_mcp_config(), transport=transport)
    tools = factory.build_tools()
    search_web = next(t for t in tools if t.name == "search_web")

    await search_web.call(query="python typing")

    assert captured.get("url") == "http://ddg.test"
    await factory.aclose()


async def test_factory_build_tools_returns_three_tool_adapters() -> None:
    """build_tools() returns the three expected tool adapter types."""
    transport = httpx.MockTransport(_make_handler())
    factory = McpClientFactory.from_config(_mcp_config(), transport=transport)

    tools = factory.build_tools()

    assert len(tools) == 3
    assert isinstance(tools[0], QueryDocsTool)
    assert isinstance(tools[1], ResolveLibraryIdTool)
    assert isinstance(tools[2], SearchWebTool)
    await factory.aclose()


async def test_factory_aclose_releases_connections() -> None:
    """aclose() calls close on both underlying HTTP clients without error."""
    transport = httpx.MockTransport(_make_handler())
    factory = McpClientFactory.from_config(_mcp_config(), transport=transport)

    await factory.aclose()
