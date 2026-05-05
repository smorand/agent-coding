"""MCP client package for agent-code.

Provides async clients for the two external MCP servers the agent uses:
- **Context7** (`resolve_library_id`, `query_docs`): up-to-date Python library docs.
- **DuckDuckGo** (`search_web`): general web search as a last resort.

Each server is wrapped by a high-level client; a `McpClientFactory` builds
both from `AgentCodeConfig.mcp` and produces `Tool` adapters ready for the
`ToolRegistry`.

Public surface:

- `McpCallResult`, `McpError`, `McpClient`: shared types.
- `AsyncMcpHttpClient`: low-level JSON-RPC transport.
- `Context7McpClient`, `DuckDuckGoMcpClient`: high-level server clients.
- `QueryDocsTool`, `ResolveLibraryIdTool`, `SearchWebTool`: Tool adapters.
- `McpClientFactory`: builds everything from config.
- `make_mcp_tools`: build tool tuple from pre-built clients.
"""

from __future__ import annotations

from mcp.base import McpCallResult, McpClient, McpError
from mcp.context7 import Context7McpClient
from mcp.duckduckgo import DuckDuckGoMcpClient
from mcp.factory import McpClientFactory
from mcp.http_client import AsyncMcpHttpClient
from mcp.tools import (
    QueryDocsTool,
    ResolveLibraryIdTool,
    SearchWebTool,
    make_mcp_tools,
)

__all__ = [
    "AsyncMcpHttpClient",
    "Context7McpClient",
    "DuckDuckGoMcpClient",
    "McpCallResult",
    "McpClient",
    "McpClientFactory",
    "McpError",
    "QueryDocsTool",
    "ResolveLibraryIdTool",
    "SearchWebTool",
    "make_mcp_tools",
]
