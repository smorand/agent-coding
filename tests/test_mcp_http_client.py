"""Tests for the MCP HTTP transport client."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from llm.retry import RetryPolicy
from mcp.base import McpCallResult, McpError
from mcp.http_client import AsyncMcpHttpClient


def _success_response(
    text: str = "some docs",
    is_error: bool = False,
    req_id: int = 1,
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": is_error,
        },
    }


def _error_response(code: int = -32600, message: str = "bad request") -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": code, "message": message},
    }


def _success_handler(text: str = "some docs") -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_response(text=text))

    return handler


@pytest.fixture(autouse=True)
def _disable_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub asyncio.sleep so retries do not slow tests down."""

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def _no_retry() -> RetryPolicy:
    return RetryPolicy(max_attempts=1)


async def test_call_tool_returns_result_on_success() -> None:
    """A 200 OK with a well-formed body produces a McpCallResult."""
    transport = httpx.MockTransport(_success_handler("hello from mcp"))
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    result = await client.call_tool("query_docs", {"library": "httpx"})

    assert isinstance(result, McpCallResult)
    assert result.text == "hello from mcp"
    assert not result.is_error
    await client.aclose()


async def test_call_tool_sends_jsonrpc_to_base_url() -> None:
    """The request body is valid JSON-RPC 2.0 sent to the configured URL."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_response())

    client = AsyncMcpHttpClient("http://mcp.test", transport=httpx.MockTransport(handler))
    await client.call_tool("my-tool", {"arg": "val"})

    assert captured["url"] == "http://mcp.test"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["jsonrpc"] == "2.0"
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "my-tool", "arguments": {"arg": "val"}}
    await client.aclose()


async def test_call_tool_increments_request_id() -> None:
    """Each call uses a unique, monotonically increasing request ID."""
    ids: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        ids.append(body["id"])
        return httpx.Response(200, json=_success_response())

    client = AsyncMcpHttpClient("http://mcp.test", transport=httpx.MockTransport(handler))
    await client.call_tool("t1", {})
    await client.call_tool("t2", {})
    await client.call_tool("t3", {})

    assert ids == sorted(ids)
    assert len(set(ids)) == 3
    await client.aclose()


async def test_call_tool_strips_trailing_slash_from_url() -> None:
    """A trailing slash in the configured URL is stripped before the POST."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_success_response())

    client = AsyncMcpHttpClient("http://mcp.test/", transport=httpx.MockTransport(handler))
    await client.call_tool("tool", {})

    assert not captured["url"].endswith("/"), f"URL still has trailing slash: {captured['url']}"
    await client.aclose()


async def test_call_tool_raises_on_jsonrpc_error() -> None:
    """A JSON-RPC error object in the response raises McpError."""
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=_error_response(-32601, "method not found")))
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    with pytest.raises(McpError, match="method not found"):
        await client.call_tool("unknown", {})
    await client.aclose()


async def test_call_tool_raises_on_missing_result_field() -> None:
    """A 200 response without a 'result' field raises McpError."""
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1}))
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    with pytest.raises(McpError, match="missing 'result'"):
        await client.call_tool("tool", {})
    await client.aclose()


async def test_call_tool_raises_on_non_json_body() -> None:
    """A non-JSON response body raises McpError."""
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, content=b"not json", headers={"Content-Type": "text/plain"})
    )
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    with pytest.raises(McpError, match="non-JSON"):
        await client.call_tool("tool", {})
    await client.aclose()


async def test_call_tool_raises_on_4xx_non_retryable() -> None:
    """A 4xx status (other than 429) raises McpError without retrying."""
    call_count = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(404)

    client = AsyncMcpHttpClient(
        "http://mcp.test",
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3),
    )

    with pytest.raises(McpError, match="status 404"):
        await client.call_tool("tool", {})
    assert call_count == 1
    await client.aclose()


async def test_call_tool_retries_on_5xx() -> None:
    """A 5xx status triggers retries up to max_attempts."""
    call_count = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=_success_response("after retry"))

    client = AsyncMcpHttpClient(
        "http://mcp.test",
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.0),
    )
    result = await client.call_tool("tool", {})

    assert result.text == "after retry"
    assert call_count == 3
    await client.aclose()


async def test_call_tool_retries_on_timeout() -> None:
    """Network timeouts are retried up to max_attempts."""
    call_count = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.TimeoutException("timed out")
        return httpx.Response(200, json=_success_response("ok after timeout"))

    client = AsyncMcpHttpClient(
        "http://mcp.test",
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.0),
    )
    result = await client.call_tool("tool", {})

    assert result.text == "ok after timeout"
    assert call_count == 2
    await client.aclose()


async def test_call_tool_exhausts_retries_raises_mcp_error() -> None:
    """When all retries fail with 503, McpError is raised."""
    transport = httpx.MockTransport(lambda _req: httpx.Response(503))
    client = AsyncMcpHttpClient(
        "http://mcp.test",
        transport=transport,
        retry=RetryPolicy(max_attempts=2, base_delay_seconds=0.0),
    )

    with pytest.raises(McpError):
        await client.call_tool("tool", {})
    await client.aclose()


async def test_call_tool_handles_is_error_true() -> None:
    """A result with isError=true is returned as McpCallResult(is_error=True)."""
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(200, json=_success_response("bad input", is_error=True))
    )
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    result = await client.call_tool("tool", {})

    assert result.is_error
    assert result.text == "bad input"
    await client.aclose()


async def test_call_tool_concatenates_multiple_text_items() -> None:
    """Multiple text content items are joined with newlines."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "image", "data": "ignored"},
                {"type": "text", "text": "part two"},
            ],
            "isError": False,
        },
    }
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json=payload))
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    result = await client.call_tool("tool", {})

    assert result.text == "part one\npart two"
    await client.aclose()


async def test_call_tool_handles_sse_response() -> None:
    """An SSE response with a result data event is parsed correctly."""
    sse_body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"sse result"}],"isError":false}}\n'
        "\n"
    )
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    result = await client.call_tool("tool", {})

    assert result.text == "sse result"
    assert not result.is_error
    await client.aclose()


async def test_call_tool_sse_raises_on_error_event() -> None:
    """An SSE stream containing a JSON-RPC error raises McpError."""
    sse_body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"invalid request"}}\n\n'
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    with pytest.raises(McpError, match="invalid request"):
        await client.call_tool("tool", {})
    await client.aclose()


async def test_call_tool_sse_raises_when_no_result_event() -> None:
    """An SSE stream that ends without a result event raises McpError."""
    sse_body = "event: comment\ndata: not json\n\n"
    transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"Content-Type": "text/event-stream"},
        )
    )
    client = AsyncMcpHttpClient("http://mcp.test", transport=transport, retry=_no_retry())

    with pytest.raises(McpError, match="SSE stream ended without a result"):
        await client.call_tool("tool", {})
    await client.aclose()
