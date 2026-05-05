"""DuckDuckGo MCP client.

Wraps the DuckDuckGo MCP server's `search` tool for general web search.
The agent uses this as a last resort when local docs and Context7 are
insufficient.

The tool name is `search` (standard for most DuckDuckGo MCP server
implementations). The agent-internal name is `search_web`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.base import McpCallResult, McpError

if TYPE_CHECKING:
    from mcp.base import McpClient

logger = logging.getLogger(__name__)

DUCKDUCKGO_TOOL_SEARCH = "search"

DEFAULT_MAX_RESULTS = 10


class DuckDuckGoMcpClient:
    """High-level DuckDuckGo client exposing `search_web`."""

    __slots__ = ("_client",)

    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def aclose(self) -> None:
        """Delegate close to the underlying transport client."""
        await self._client.aclose()

    async def search_web(
        self,
        query: str,
        *,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> McpCallResult:
        """Search the web for `query` and return the top results.

        `max_results` caps the number of returned results. Raises `McpError`
        on transport failure or if the query is empty.
        """
        if not query:
            msg = "query must not be empty"
            raise McpError(msg)
        arguments: dict[str, Any] = {"query": query, "maxResults": max_results}
        logger.debug("DuckDuckGo search for %r (max_results=%d)", query, max_results)
        return await self._client.call_tool(DUCKDUCKGO_TOOL_SEARCH, arguments)
