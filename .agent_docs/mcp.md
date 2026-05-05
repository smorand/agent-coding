# MCP Clients (FR-016 adjacent)

> Async clients for the two external MCP servers the agent uses:
> Context7 (library documentation) and DuckDuckGo (web search).
> Both are wired into the `ToolRegistry` so phases call them like any
> other tool, with no awareness of the underlying transport.

## Files

- `src/mcp/base.py`: shared types.
  - `McpCallResult(text, is_error)`: frozen value object. `text` is the
    concatenated text from all `type=text` content items in the MCP response.
  - `McpError`: transport-level error (after retries). Has `retryable` and
    `status_code` attributes.
  - `McpClient`: structural Protocol (`call_tool`, `aclose`).
- `src/mcp/http_client.py`: `AsyncMcpHttpClient`.
  - Sends JSON-RPC 2.0 `tools/call` POSTs to the configured URL.
  - Handles both `application/json` and `text/event-stream` (SSE) responses.
  - Retries 5xx and 429 with the same `RetryPolicy` / `sleep_for_backoff`
    used by the LLM client. Network errors (`TimeoutException`,
    `TransportError`) are also retried.
  - Increments a per-instance request ID for each call.
  - Emits an OpenTelemetry span `mcp.call_tool` with `mcp.endpoint`,
    `mcp.tool`, `mcp.duration_ms`, `mcp.is_error` attributes.
- `src/mcp/context7.py`: `Context7McpClient`.
  - `resolve_library_id(library_name)` → `resolve-library-id`
  - `query_docs(library_id, *, topic="", tokens=5000)` → `get-library-docs`
  - Validates non-empty arguments before calling transport; raises `McpError`.
- `src/mcp/duckduckgo.py`: `DuckDuckGoMcpClient`.
  - `search_web(query, *, max_results=10)` → `search`
  - Validates non-empty query; raises `McpError`.
- `src/mcp/tools.py`: Tool Protocol adapters for the registry.
  - `QueryDocsTool` (name: `query_docs`) wraps `Context7McpClient.query_docs`.
  - `ResolveLibraryIdTool` (name: `resolve_library_id`) wraps `Context7McpClient.resolve_library_id`.
  - `SearchWebTool` (name: `search_web`) wraps `DuckDuckGoMcpClient.search_web`.
  - All three catch `McpError` and return `ToolResult(ok=False)` instead of
    propagating. An `is_error=True` MCP result also returns `ok=False`.
  - `make_mcp_tools(context7, duckduckgo)` builds all three at once.
- `src/mcp/factory.py`: `McpClientFactory`.
  - `from_config(mcp_config, *, retry, transport)` builds both clients from
    the parsed `AgentCodeConfig.mcp` section.
  - `build_tools()` returns the three Tool adapters ready for the registry.
  - `aclose()` closes both HTTP connections.

## MCP JSON-RPC transport

All calls go to the configured base URL as a single POST:

```
POST <url>
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "id": <int>,
  "method": "tools/call",
  "params": {
    "name": "<mcp-tool-name>",
    "arguments": { ... }
  }
}
```

The server responds with either:
- `Content-Type: application/json` — a JSON-RPC response object.
- `Content-Type: text/event-stream` — an SSE stream; the client scans
  `data:` lines for the first JSON object containing `"result"` or `"error"`.

## Configuration

Both servers are declared in `config.yaml` under `mcp:`:

```yaml
mcp:
  context7:
    url: http://context7:9000
  duckduckgo:
    url: http://duckduckgo:9001
```

The `url` field is the exact POST target. No path suffix is added.

## Context7 tool names

| Agent tool | MCP server tool | Key arguments |
|---|---|---|
| `resolve_library_id(library)` | `resolve-library-id` | `libraryName` |
| `query_docs(library, topic, tokens)` | `get-library-docs` | `context7CompatibleLibraryID`, `topic`, `tokens` |

## DuckDuckGo tool name

| Agent tool | MCP server tool | Key arguments |
|---|---|---|
| `search_web(query, max_results)` | `search` | `query`, `maxResults` |

## Wiring into the pipeline (pending)

`McpClientFactory` is not yet wired into `_build_pipeline_from_config`. The
follow-up PR will:
1. Construct `McpClientFactory.from_config(config.mcp)` when the config has
   an `mcp` section.
2. Inject `factory.build_tools()` into the `ToolRegistry` alongside the
   existing file/git/search tools.
3. Call `factory.aclose()` after the orchestrator run completes.

## Testing

- `tests/test_mcp_http_client.py` (17 tests): JSON and SSE response parsing,
  request ID increments, trailing slash stripping, retry on 5xx and timeout,
  non-retryable 4xx, error responses.
- `tests/test_mcp_context7.py` (9 tests): tool name routing, argument
  mapping, empty-string guards, is_error passthrough, aclose delegation.
- `tests/test_mcp_duckduckgo.py` (8 tests): tool name routing, argument
  mapping, empty-string guard, is_error passthrough, aclose delegation.
- `tests/test_mcp_tools.py` (14 tests): ok/not-ok ToolResult mapping for all
  three adapters, McpError → not-ok conversion, `make_mcp_tools` output.
- `tests/test_mcp_factory.py` (4 tests): URL routing per client,
  `build_tools` return types, `aclose` no-error.
