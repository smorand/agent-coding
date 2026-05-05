"""Async HTTP client for the MCP Streamable HTTP transport.

Sends JSON-RPC 2.0 `tools/call` requests to any MCP-compliant endpoint.
Handles both `application/json` and `text/event-stream` (SSE) response
formats. Retries transient HTTP failures with exponential backoff using
the same `RetryPolicy` as the LLM client.

MCP Streamable HTTP spec reference:
  https://spec.modelcontextprotocol.io/specification/2024-11-05/basic/transports/
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from llm.retry import DEFAULT_RETRY, RetryPolicy, sleep_for_backoff
from mcp.base import McpCallResult, McpError
from tracing import trace_span

logger = logging.getLogger(__name__)

JSONRPC_VERSION = "2.0"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_SSE = "text/event-stream"
ACCEPT_HEADER = "application/json, text/event-stream"

HTTP_BAD_REQUEST = 400
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500


class AsyncMcpHttpClient:
    """Async JSON-RPC 2.0 client targeting a single MCP Streamable HTTP endpoint.

    Constructed once per server URL via `McpClientFactory`. Holds a single
    `httpx.AsyncClient` for connection reuse; close with `aclose()`.
    """

    __slots__ = ("_http", "_owns_http", "_request_id", "_retry", "_url")

    def __init__(
        self,
        url: str,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._url = url.rstrip("/")
        self._retry = retry or DEFAULT_RETRY
        self._request_id = 0
        client_kwargs: dict[str, Any] = {"timeout": timeout_seconds}
        if transport is not None:
            client_kwargs["transport"] = transport
        self._http = httpx.AsyncClient(**client_kwargs)
        self._owns_http = True

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
        """Call `tool_name` on the MCP server and return the result."""
        self._request_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        attributes: dict[str, str | int | float] = {
            "mcp.endpoint": self._url,
            "mcp.tool": tool_name,
        }
        with trace_span("mcp.call_tool", attributes=attributes) as span:
            start = time.monotonic()
            response = await self._post_with_retry(payload)
            duration_ms = (time.monotonic() - start) * 1000.0
            span.set_attribute("mcp.duration_ms", duration_ms)
            result = self._parse_response(response)
            span.set_attribute("mcp.is_error", result.is_error)
            return result

    async def _post_with_retry(self, payload: dict[str, Any]) -> httpx.Response:
        last_error: McpError | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = await self._http.post(
                    self._url,
                    json=payload,
                    headers={"Content-Type": CONTENT_TYPE_JSON, "Accept": ACCEPT_HEADER},
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = McpError(f"Network error during MCP call: {exc}", retryable=True)
                if attempt >= self._retry.max_attempts:
                    break
                await sleep_for_backoff(attempt, self._retry)
                continue
            if response.status_code < HTTP_BAD_REQUEST:
                return response
            retryable = response.status_code >= HTTP_SERVER_ERROR or response.status_code == HTTP_TOO_MANY_REQUESTS
            last_error = McpError(
                f"MCP endpoint returned status {response.status_code}",
                retryable=retryable,
                status_code=response.status_code,
            )
            if not retryable or attempt >= self._retry.max_attempts:
                break
            await sleep_for_backoff(attempt, self._retry)
        if last_error is None:
            msg = "Retry loop exited without an outcome (unreachable)"
            raise McpError(msg)
        raise last_error

    def _parse_response(self, response: httpx.Response) -> McpCallResult:
        content_type = response.headers.get("content-type", "")
        if CONTENT_TYPE_SSE in content_type:
            return self._parse_sse_response(response.text)
        return self._parse_json_response(response)

    def _parse_json_response(self, response: httpx.Response) -> McpCallResult:
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            msg = f"MCP endpoint returned non-JSON body (status {response.status_code})"
            raise McpError(msg, status_code=response.status_code) from exc
        if "error" in data:
            err = data["error"]
            code = err.get("code", "")
            message = err.get("message", str(err))
            raise McpError(f"MCP server error {code}: {message}")
        result = data.get("result")
        if result is None:
            msg = f"MCP response missing 'result' field (status {response.status_code})"
            raise McpError(msg, status_code=response.status_code)
        return _result_from_dict(result)

    def _parse_sse_response(self, text: str) -> McpCallResult:
        """Extract the JSON-RPC result from an SSE stream."""
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            try:
                event_data: dict[str, Any] = json.loads(raw)
            except ValueError:
                continue
            if "result" in event_data:
                return _result_from_dict(event_data["result"])
            if "error" in event_data:
                err = event_data["error"]
                code = err.get("code", "")
                message = err.get("message", str(err))
                raise McpError(f"MCP server error via SSE {code}: {message}")
        msg = "SSE stream ended without a result event"
        raise McpError(msg)


def _result_from_dict(result: dict[str, Any]) -> McpCallResult:
    """Convert a JSON-RPC result dict to a `McpCallResult`."""
    raw_content: list[dict[str, Any]] = result.get("content") or []
    is_error: bool = bool(result.get("isError", False))
    parts = [item.get("text", "") for item in raw_content if item.get("type") == "text"]
    text = "\n".join(parts)
    return McpCallResult(text=text, is_error=is_error)
