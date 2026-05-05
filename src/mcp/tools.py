"""Tool Protocol adapters for MCP clients.

Each class wraps one MCP operation and exposes it as a `Tool` (structural
type from `tools.base`). Instances are registered in the `ToolRegistry`
under the snake_case names used by the phases:

- `query_docs`         (Context7: get-library-docs)
- `resolve_library_id` (Context7: resolve-library-id)
- `search_web`         (DuckDuckGo: search)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.base import McpError
from tools.base import ToolResult

if TYPE_CHECKING:
    from mcp.context7 import Context7McpClient
    from mcp.duckduckgo import DuckDuckGoMcpClient

logger = logging.getLogger(__name__)


class QueryDocsTool:
    """Fetch up-to-date documentation for a library via Context7.

    Parameters
    ----------
    library:
        A Context7-compatible library ID (e.g. `/uv`, `/httpx`).
    topic:
        Optional documentation topic to scope the query.
    tokens:
        Maximum documentation length (default: 5000).
    """

    name: str = "query_docs"
    description: str = (
        "Fetch up-to-date documentation for a Python library via Context7. "
        "Pass the Context7 library ID (use resolve_library_id first if unknown). "
        "Optionally scope with a topic."
    )

    __slots__ = ("_context7",)

    def __init__(self, context7: Context7McpClient) -> None:
        self._context7 = context7

    async def call(self, *, library: str, topic: str = "", tokens: int = 5000) -> ToolResult:
        """Fetch library docs. Returns ToolResult with the documentation text."""
        try:
            result = await self._context7.query_docs(library, topic=topic, tokens=tokens)
        except McpError as exc:
            logger.warning("query_docs failed for %r: %s", library, exc)
            return ToolResult(ok=False, error=str(exc))
        if result.is_error:
            return ToolResult(ok=False, error=result.text)
        return ToolResult(ok=True, output=result.text)


class ResolveLibraryIdTool:
    """Resolve the canonical Context7 library ID for a package name.

    Parameters
    ----------
    library:
        The library name (e.g. `httpx`, `pydantic`).
    """

    name: str = "resolve_library_id"
    description: str = (
        "Find the canonical Context7-compatible library ID for a Python package. "
        "Use this before query_docs when you only know the package name."
    )

    __slots__ = ("_context7",)

    def __init__(self, context7: Context7McpClient) -> None:
        self._context7 = context7

    async def call(self, *, library: str) -> ToolResult:
        """Resolve library name to a Context7 ID."""
        try:
            result = await self._context7.resolve_library_id(library)
        except McpError as exc:
            logger.warning("resolve_library_id failed for %r: %s", library, exc)
            return ToolResult(ok=False, error=str(exc))
        if result.is_error:
            return ToolResult(ok=False, error=result.text)
        return ToolResult(ok=True, output=result.text)


class SearchWebTool:
    """Search the web via DuckDuckGo.

    Parameters
    ----------
    query:
        The search query.
    max_results:
        Maximum number of results to return (default: 10).
    """

    name: str = "search_web"
    description: str = (
        "Search the web using DuckDuckGo. Use as a last resort when local docs and Context7 are insufficient."
    )

    __slots__ = ("_duckduckgo",)

    def __init__(self, duckduckgo: DuckDuckGoMcpClient) -> None:
        self._duckduckgo = duckduckgo

    async def call(self, *, query: str, max_results: int = 10) -> ToolResult:
        """Search the web. Returns ToolResult with search results."""
        try:
            result = await self._duckduckgo.search_web(query, max_results=max_results)
        except McpError as exc:
            logger.warning("search_web failed for %r: %s", query, exc)
            return ToolResult(ok=False, error=str(exc))
        if result.is_error:
            return ToolResult(ok=False, error=result.text)
        return ToolResult(ok=True, output=result.text)


def make_mcp_tools(
    context7: Context7McpClient,
    duckduckgo: DuckDuckGoMcpClient,
) -> tuple[QueryDocsTool, ResolveLibraryIdTool, SearchWebTool]:
    """Build the three MCP tool adapters ready to register in a ToolRegistry."""
    return (
        QueryDocsTool(context7),
        ResolveLibraryIdTool(context7),
        SearchWebTool(duckduckgo),
    )
