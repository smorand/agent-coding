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
from typing import TYPE_CHECKING, Any

from mcp.base import McpError
from tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from mcp.context7 import Context7McpClient
    from mcp.duckduckgo import DuckDuckGoMcpClient

logger = logging.getLogger(__name__)

DEFAULT_DOCS_TOKENS = 5000
DEFAULT_SEARCH_RESULTS = 10


def _require_str(kwargs: dict[str, Any], key: str) -> str | ToolResult:
    """Extract a non-empty string from kwargs or return a ToolResult error."""
    value = kwargs.get(key)
    if not isinstance(value, str) or not value:
        return ToolResult(ok=False, error=f"argument {key!r}: required non-empty string")
    return value


class QueryDocsTool(Tool):
    """Fetch up-to-date documentation for a library via Context7.

    Expected kwargs (passed by the registry):
    - `library` (str, required): a Context7-compatible library ID.
    - `topic` (str, optional): scope the query to a topic.
    - `tokens` (int, optional): max documentation length (default 5000).
    """

    name = "query_docs"
    description = (
        "Fetch up-to-date documentation for a Python library via Context7. "
        "Pass the Context7 library ID (use resolve_library_id first if unknown). "
        "Optionally scope with a topic."
    )

    __slots__ = ("_context7",)

    def __init__(self, context7: Context7McpClient) -> None:
        self._context7 = context7

    async def call(self, **kwargs: Any) -> ToolResult:
        """Fetch library docs. Returns ToolResult with the documentation text."""
        library = _require_str(kwargs, "library")
        if isinstance(library, ToolResult):
            return library
        topic = kwargs.get("topic", "") or ""
        tokens = kwargs.get("tokens", DEFAULT_DOCS_TOKENS)
        try:
            result = await self._context7.query_docs(library, topic=topic, tokens=tokens)
        except McpError as exc:
            logger.warning("query_docs failed for %r: %s", library, exc)
            return ToolResult(ok=False, error=str(exc))
        if result.is_error:
            return ToolResult(ok=False, error=result.text)
        return ToolResult(ok=True, output=result.text)


class ResolveLibraryIdTool(Tool):
    """Resolve the canonical Context7 library ID for a package name.

    Expected kwargs:
    - `library` (str, required): the library name (e.g. `httpx`, `pydantic`).
    """

    name = "resolve_library_id"
    description = (
        "Find the canonical Context7-compatible library ID for a Python package. "
        "Use this before query_docs when you only know the package name."
    )

    __slots__ = ("_context7",)

    def __init__(self, context7: Context7McpClient) -> None:
        self._context7 = context7

    async def call(self, **kwargs: Any) -> ToolResult:
        """Resolve library name to a Context7 ID."""
        library = _require_str(kwargs, "library")
        if isinstance(library, ToolResult):
            return library
        try:
            result = await self._context7.resolve_library_id(library)
        except McpError as exc:
            logger.warning("resolve_library_id failed for %r: %s", library, exc)
            return ToolResult(ok=False, error=str(exc))
        if result.is_error:
            return ToolResult(ok=False, error=result.text)
        return ToolResult(ok=True, output=result.text)


class SearchWebTool(Tool):
    """Search the web via DuckDuckGo.

    Expected kwargs:
    - `query` (str, required): the search query.
    - `max_results` (int, optional): maximum results to return (default 10).
    """

    name = "search_web"
    description = "Search the web using DuckDuckGo. Use as a last resort when local docs and Context7 are insufficient."

    __slots__ = ("_duckduckgo",)

    def __init__(self, duckduckgo: DuckDuckGoMcpClient) -> None:
        self._duckduckgo = duckduckgo

    async def call(self, **kwargs: Any) -> ToolResult:
        """Search the web. Returns ToolResult with search results."""
        query = _require_str(kwargs, "query")
        if isinstance(query, ToolResult):
            return query
        max_results = kwargs.get("max_results", DEFAULT_SEARCH_RESULTS)
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
) -> tuple[Tool, ...]:
    """Build the three MCP tool adapters ready to register in a ToolRegistry."""
    return (
        QueryDocsTool(context7),
        ResolveLibraryIdTool(context7),
        SearchWebTool(duckduckgo),
    )
