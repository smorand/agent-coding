"""Context7 MCP client.

Wraps the Context7 MCP server's two tools:
- `resolve-library-id`: find the canonical Context7 library ID for a package.
- `get-library-docs`: fetch up-to-date documentation for a library.

Context7 MCP server: https://github.com/upstash/context7

The tool names use kebab-case as required by the Context7 server; the
agent-internal names (`resolve_library_id`, `query_docs`) are snake_case.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from mcp.base import McpCallResult, McpError

if TYPE_CHECKING:
    from mcp.base import McpClient

logger = logging.getLogger(__name__)

CONTEXT7_TOOL_RESOLVE = "resolve-library-id"
CONTEXT7_TOOL_DOCS = "get-library-docs"

DEFAULT_DOCS_TOKENS = 5000


class Context7McpClient:
    """High-level Context7 client exposing `resolve_library_id` and `query_docs`."""

    __slots__ = ("_client",)

    def __init__(self, client: McpClient) -> None:
        self._client = client

    async def aclose(self) -> None:
        """Delegate close to the underlying transport client."""
        await self._client.aclose()

    async def resolve_library_id(self, library_name: str) -> McpCallResult:
        """Find the canonical Context7-compatible library ID for `library_name`.

        Returns a `McpCallResult` whose `text` contains the library ID
        (e.g. `/uv`, `/httpx`). Raises `McpError` on transport failure.
        """
        if not library_name:
            msg = "library_name must not be empty"
            raise McpError(msg)
        arguments: dict[str, Any] = {"libraryName": library_name}
        logger.debug("Context7 resolve-library-id for %r", library_name)
        return await self._client.call_tool(CONTEXT7_TOOL_RESOLVE, arguments)

    async def query_docs(
        self,
        library_id: str,
        *,
        topic: str = "",
        tokens: int = DEFAULT_DOCS_TOKENS,
    ) -> McpCallResult:
        """Fetch documentation for `library_id`, optionally scoped to `topic`.

        `library_id` should be a Context7-compatible ID (e.g. `/uv`).
        `tokens` controls the maximum length of the returned documentation.
        Raises `McpError` on transport failure.
        """
        if not library_id:
            msg = "library_id must not be empty"
            raise McpError(msg)
        arguments: dict[str, Any] = {
            "context7CompatibleLibraryID": library_id,
            "tokens": tokens,
        }
        if topic:
            arguments["topic"] = topic
        logger.debug("Context7 get-library-docs for %r (topic=%r)", library_id, topic)
        return await self._client.call_tool(CONTEXT7_TOOL_DOCS, arguments)
