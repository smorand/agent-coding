"""MCP client factory.

Creates Context7 and DuckDuckGo clients from the parsed `AgentCodeConfig.mcp`
section. Each client wraps an `AsyncMcpHttpClient` targeting its configured
URL. The factory also builds the three Tool adapters ready for the
`ToolRegistry`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.context7 import Context7McpClient
from mcp.duckduckgo import DuckDuckGoMcpClient
from mcp.http_client import AsyncMcpHttpClient
from mcp.tools import make_mcp_tools

if TYPE_CHECKING:
    import httpx

    from config_loader import McpConfig
    from llm.retry import RetryPolicy
    from tools.base import Tool

logger = logging.getLogger(__name__)


class McpClientFactory:
    """Builds MCP clients and tool adapters from `McpConfig`.

    Intended usage:

    ```python
    factory = McpClientFactory.from_config(config.mcp)
    tools = factory.build_tools()
    registry = ToolRegistry([*existing_tools, *tools])
    ```

    Call `aclose()` when the run finishes to release HTTP connections.
    """

    __slots__ = ("_context7", "_duckduckgo")

    def __init__(
        self,
        context7: Context7McpClient,
        duckduckgo: DuckDuckGoMcpClient,
    ) -> None:
        self._context7 = context7
        self._duckduckgo = duckduckgo

    @classmethod
    def from_config(
        cls,
        mcp_config: McpConfig,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> McpClientFactory:
        """Construct clients from the parsed MCP configuration section."""
        context7_http = AsyncMcpHttpClient(
            mcp_config.context7.url,
            retry=retry,
            transport=transport,
        )
        duckduckgo_http = AsyncMcpHttpClient(
            mcp_config.duckduckgo.url,
            retry=retry,
            transport=transport,
        )
        logger.info(
            "MCP clients configured: context7=%s duckduckgo=%s",
            mcp_config.context7.url,
            mcp_config.duckduckgo.url,
        )
        return cls(
            context7=Context7McpClient(context7_http),
            duckduckgo=DuckDuckGoMcpClient(duckduckgo_http),
        )

    def build_tools(self) -> tuple[Tool, ...]:
        """Return the three MCP tool adapters for the ToolRegistry."""
        return make_mcp_tools(self._context7, self._duckduckgo)

    async def aclose(self) -> None:
        """Close all underlying HTTP connections."""
        await self._context7.aclose()
        await self._duckduckgo.aclose()
