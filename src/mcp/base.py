"""MCP client contract and shared value objects.

Phases consume MCP tools through the ToolRegistry. The underlying transport
(HTTP JSON-RPC) is hidden behind `McpClient`. Inputs and outputs are immutable
value objects to keep the interface easy to mock and reason about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class McpCallResult:
    """Result from a single MCP tool call.

    `text` is the concatenated text from all `type=text` content items.
    `is_error` reflects the MCP server's own `isError` flag (distinct from
    a transport-level `McpError`; this means the tool ran but reported a
    logical error in its response body).
    """

    text: str
    is_error: bool = False


class McpError(Exception):
    """Raised when an MCP tool call fails irrecoverably (after retries)."""

    __slots__ = ("retryable", "status_code")

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@runtime_checkable
class McpClient(Protocol):
    """Structural type for MCP clients targeting a single server.

    Implementations wrap one MCP server and translate its tools into
    async calls returning `McpCallResult`. They are constructed once per
    run via `McpClientFactory` and closed with `aclose()`.
    """

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Call `tool_name` with `arguments`. Raises `McpError` on failure."""
        ...

    async def aclose(self) -> None:
        """Release underlying resources (HTTP connections, etc.)."""
        ...
