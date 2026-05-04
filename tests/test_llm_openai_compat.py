"""Tests for the OpenAI-compatible LLM client."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from config_loader import PhaseModelConfig
from llm.base import ChatMessage, FinishReason, LlmError, Role
from llm.openai_compat import OpenAICompatClient
from llm.retry import RetryPolicy


def _phase_config(api_key_env: str | None = None) -> PhaseModelConfig:
    return PhaseModelConfig(
        url="http://vllm.test/v1",
        model_name="qwen3-32b",
        api_key_env=api_key_env,
    )


def _success_payload(content: str = "hello", model: str = "qwen3-32b") -> dict[str, object]:
    return {
        "model": model,
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            },
        ],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }


def _success_handler(content: str = "hello", model: str = "qwen3-32b") -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload(content=content, model=model))

    return handler


@pytest.fixture(autouse=True)
def _disable_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub asyncio.sleep so retries do not slow tests down."""

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


async def test_complete_returns_chat_response_with_token_usage() -> None:
    """A 200 OK with a well-formed body produces a populated ChatResponse."""
    transport = httpx.MockTransport(_success_handler(content="hi", model="qwen3-32b"))
    client = OpenAICompatClient(_phase_config(), transport=transport)

    response = await client.complete([ChatMessage(role=Role.USER, content="ping")])

    assert response.content == "hi"
    assert response.model == "qwen3-32b"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert response.finish_reason == FinishReason.STOP
    assert response.duration_ms >= 0
    await client.aclose()


async def test_complete_sends_payload_and_uses_chat_completions_path() -> None:
    """Request body contains model and messages; URL path is /chat/completions."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_payload())

    client = OpenAICompatClient(_phase_config(), transport=httpx.MockTransport(handler))
    await client.complete(
        [ChatMessage(role=Role.SYSTEM, content="be brief"), ChatMessage(role=Role.USER, content="hi")]
    )

    assert captured["url"] == "http://vllm.test/v1/chat/completions"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "qwen3-32b"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    await client.aclose()


async def test_complete_adds_authorization_header_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `api_key_env` is set, the value is sent as Bearer in Authorization."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        captured["auth"] = auth
        return httpx.Response(200, json=_success_payload())

    monkeypatch.setenv("VLLM_TOKEN", "secret-123")
    client = OpenAICompatClient(
        _phase_config(api_key_env="VLLM_TOKEN"),
        transport=httpx.MockTransport(handler),
    )
    await client.complete([ChatMessage(role=Role.USER, content="ping")])

    assert captured["auth"] == "Bearer secret-123"
    await client.aclose()


async def test_complete_raises_when_api_key_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset api_key_env is reported as LlmError before the HTTP call."""
    monkeypatch.delenv("MISSING_TOKEN", raising=False)

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_success_payload())

    client = OpenAICompatClient(
        _phase_config(api_key_env="MISSING_TOKEN"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LlmError, match="MISSING_TOKEN"):
        await client.complete([ChatMessage(role=Role.USER, content="ping")])
    await client.aclose()


async def test_complete_retries_on_5xx_then_succeeds() -> None:
    """A 503 followed by a 200 yields a successful response after one retry."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(200, json=_success_payload())

    client = OpenAICompatClient(
        _phase_config(),
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )
    response = await client.complete([ChatMessage(role=Role.USER, content="ping")])

    assert response.content == "hello"
    assert state["calls"] == 2
    await client.aclose()


async def test_complete_retries_on_429() -> None:
    """A 429 (rate limited) is retried like a 5xx."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(429, json={"error": "rate limit"})
        return httpx.Response(200, json=_success_payload())

    client = OpenAICompatClient(
        _phase_config(),
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )
    await client.complete([ChatMessage(role=Role.USER, content="ping")])
    assert state["calls"] == 2
    await client.aclose()


async def test_complete_does_not_retry_on_4xx_other_than_429() -> None:
    """A 400 fails immediately without retry."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    client = OpenAICompatClient(
        _phase_config(),
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=5, base_delay_seconds=0.01),
    )
    with pytest.raises(LlmError) as excinfo:
        await client.complete([ChatMessage(role=Role.USER, content="ping")])
    assert state["calls"] == 1
    assert excinfo.value.status_code == 400
    assert excinfo.value.retryable is False
    await client.aclose()


async def test_complete_exhausts_max_attempts_on_persistent_failure() -> None:
    """All attempts return 500; the client raises LlmError after max_attempts."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        return httpx.Response(500, json={"error": "boom"})

    client = OpenAICompatClient(
        _phase_config(),
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )
    with pytest.raises(LlmError) as excinfo:
        await client.complete([ChatMessage(role=Role.USER, content="ping")])
    assert state["calls"] == 3
    assert excinfo.value.status_code == 500
    await client.aclose()


async def test_complete_retries_on_transport_error() -> None:
    """A connection error is retried, then succeeds on the second attempt."""
    state = {"calls": 0}

    def handler(_req: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            msg = "connection refused"
            raise httpx.ConnectError(msg)
        return httpx.Response(200, json=_success_payload())

    client = OpenAICompatClient(
        _phase_config(),
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
    )
    response = await client.complete([ChatMessage(role=Role.USER, content="ping")])
    assert response.content == "hello"
    assert state["calls"] == 2
    await client.aclose()


async def test_complete_raises_when_endpoint_returns_no_choices() -> None:
    """A 200 OK with an empty choices array is rejected as malformed."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "usage": {}})

    client = OpenAICompatClient(_phase_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmError, match="no choices"):
        await client.complete([ChatMessage(role=Role.USER, content="ping")])
    await client.aclose()


async def test_complete_raises_when_response_is_not_json() -> None:
    """A 200 with a non-JSON body is reported as a parse error."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client = OpenAICompatClient(_phase_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(LlmError, match="non-JSON"):
        await client.complete([ChatMessage(role=Role.USER, content="ping")])
    await client.aclose()


async def test_complete_passes_max_tokens_and_temperature_overrides() -> None:
    """Per-call overrides take precedence over the phase-config defaults."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_success_payload())

    config = PhaseModelConfig(
        url="http://vllm.test/v1",
        model_name="qwen3-32b",
        max_tokens=100,
        temperature=0.5,
    )
    client = OpenAICompatClient(config, transport=httpx.MockTransport(handler))
    await client.complete(
        [ChatMessage(role=Role.USER, content="ping")],
        max_tokens=50,
        temperature=0.1,
    )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 50
    assert body["temperature"] == 0.1
    await client.aclose()


async def test_complete_handles_unknown_finish_reason_gracefully() -> None:
    """A finish_reason the enum does not know becomes UNKNOWN, not an error."""

    def handler(_req: httpx.Request) -> httpx.Response:
        payload = _success_payload()
        payload["choices"][0]["finish_reason"] = "made_up_reason"  # type: ignore[index]
        return httpx.Response(200, json=payload)

    client = OpenAICompatClient(_phase_config(), transport=httpx.MockTransport(handler))
    response = await client.complete([ChatMessage(role=Role.USER, content="ping")])
    assert response.finish_reason == FinishReason.UNKNOWN
    await client.aclose()
