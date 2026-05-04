"""OpenAI-compatible chat-completion client.

Speaks any endpoint that exposes the OpenAI `/chat/completions` schema:
vLLM, Text Generation Inference, llama.cpp server, OpenAI itself, etc. The
client is async, instrumented with OpenTelemetry, and retries transient
failures with exponential backoff.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING, Any

import httpx

from llm.base import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    LlmClient,
    LlmError,
    TokenUsage,
)
from llm.retry import DEFAULT_RETRY, RetryPolicy, sleep_for_backoff
from tracing import trace_span

if TYPE_CHECKING:
    from collections.abc import Sequence

    from config_loader import PhaseModelConfig

logger = logging.getLogger(__name__)

CHAT_COMPLETIONS_PATH = "/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 60.0

HTTP_BAD_REQUEST = 400
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500


class OpenAICompatClient(LlmClient):
    """Async chat client targeting one OpenAI-compatible endpoint.

    Constructed once per phase via `PhaseLlmFactory`. Holds a single
    `httpx.AsyncClient` for connection reuse; close with `aclose()`.
    """

    __slots__ = ("_config", "_http", "_owns_http", "_retry")

    def __init__(
        self,
        config: PhaseModelConfig,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._config = config
        self._retry = retry or DEFAULT_RETRY
        client_kwargs: dict[str, Any] = {
            "base_url": config.url.rstrip("/"),
            "timeout": timeout_seconds,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._http = httpx.AsyncClient(**client_kwargs)
        self._owns_http = True

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        if self._owns_http:
            await self._http.aclose()

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Send `messages` and return the model's reply, with retries."""
        payload = self._build_payload(messages, max_tokens=max_tokens, temperature=temperature)
        headers = self._build_headers()
        attributes: dict[str, str | int | float] = {
            "llm.model": self._config.model_name,
            "llm.endpoint": self._config.url,
        }
        with trace_span("llm.complete", attributes=attributes) as span:
            start = time.monotonic()
            response = await self._post_with_retry(payload=payload, headers=headers)
            duration_ms = (time.monotonic() - start) * 1000.0
            chat_response = self._parse_response(response, duration_ms=duration_ms)
            span.set_attribute("llm.input_tokens", chat_response.usage.input_tokens)
            span.set_attribute("llm.output_tokens", chat_response.usage.output_tokens)
            span.set_attribute("llm.duration_ms", chat_response.duration_ms)
            span.set_attribute("llm.finish_reason", chat_response.finish_reason.value)
            return chat_response

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key_env:
            token = os.environ.get(self._config.api_key_env)
            if not token:
                msg = f"Environment variable {self._config.api_key_env} is unset or empty"
                raise LlmError(msg)
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _build_payload(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model_name,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
        }
        effective_max = max_tokens if max_tokens is not None else self._config.max_tokens
        if effective_max is not None:
            payload["max_tokens"] = effective_max
        effective_temperature = temperature if temperature is not None else self._config.temperature
        if effective_temperature is not None:
            payload["temperature"] = effective_temperature
        return payload

    def _parse_response(self, response: httpx.Response, *, duration_ms: float) -> ChatResponse:
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            msg = f"Endpoint returned non-JSON body (status {response.status_code})"
            raise LlmError(msg, status_code=response.status_code) from exc
        choices = data.get("choices") or []
        if not choices:
            msg = f"Endpoint returned no choices (status {response.status_code})"
            raise LlmError(msg, status_code=response.status_code)
        first = choices[0]
        message = first.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            msg = f"Endpoint returned a choice without text content (status {response.status_code})"
            raise LlmError(msg, status_code=response.status_code)
        usage_data = data.get("usage") or {}
        usage = TokenUsage(
            input_tokens=int(usage_data.get("prompt_tokens", 0)),
            output_tokens=int(usage_data.get("completion_tokens", 0)),
        )
        finish_raw = first.get("finish_reason") or FinishReason.UNKNOWN.value
        try:
            finish = FinishReason(finish_raw)
        except ValueError:
            finish = FinishReason.UNKNOWN
        model = str(data.get("model", self._config.model_name))
        return ChatResponse(
            content=content,
            usage=usage,
            model=model,
            finish_reason=finish,
            duration_ms=duration_ms,
        )

    async def _post_with_retry(
        self,
        *,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        last_error: LlmError | None = None
        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                response = await self._http.post(
                    CHAT_COMPLETIONS_PATH,
                    json=payload,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = LlmError(f"Network error during LLM call: {exc}", retryable=True)
                if attempt >= self._retry.max_attempts:
                    break
                await sleep_for_backoff(attempt, self._retry)
                continue
            if response.status_code < HTTP_BAD_REQUEST:
                return response
            retryable = response.status_code >= HTTP_SERVER_ERROR or response.status_code == HTTP_TOO_MANY_REQUESTS
            last_error = LlmError(
                f"LLM endpoint returned status {response.status_code}",
                retryable=retryable,
                status_code=response.status_code,
            )
            if not retryable or attempt >= self._retry.max_attempts:
                break
            await sleep_for_backoff(attempt, self._retry)
        if last_error is None:
            msg = "Retry loop exited without an outcome (unreachable)"
            raise LlmError(msg)
        raise last_error
