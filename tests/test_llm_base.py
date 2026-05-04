"""Tests for the LLM value objects and exception type."""

from __future__ import annotations

import dataclasses

from llm.base import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    LlmError,
    Role,
    TokenUsage,
)


def test_token_usage_total_sums_input_and_output() -> None:
    """`total_tokens` is the simple sum."""
    usage = TokenUsage(input_tokens=12, output_tokens=34)
    assert usage.total_tokens == 46


def test_chat_message_is_immutable() -> None:
    """ChatMessage is a frozen dataclass; mutation raises FrozenInstanceError."""
    msg = ChatMessage(role=Role.USER, content="hi")
    try:
        msg.content = "bye"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    msg_text = "ChatMessage should be immutable"
    raise AssertionError(msg_text)


def test_chat_response_carries_metadata() -> None:
    """ChatResponse round-trips its fields without transformation."""
    response = ChatResponse(
        content="ok",
        usage=TokenUsage(input_tokens=1, output_tokens=2),
        model="qwen3-32b",
        finish_reason=FinishReason.STOP,
        duration_ms=12.5,
    )
    assert response.content == "ok"
    assert response.usage.total_tokens == 3
    assert response.model == "qwen3-32b"
    assert response.finish_reason == FinishReason.STOP
    assert response.duration_ms == 12.5


def test_llm_error_carries_retryable_and_status_code() -> None:
    """LlmError exposes retryable and status_code attributes."""
    err = LlmError("server is down", retryable=True, status_code=503)
    assert err.retryable is True
    assert err.status_code == 503
    assert "server is down" in str(err)


def test_llm_error_defaults_to_non_retryable() -> None:
    """The LlmError default is a hard failure."""
    err = LlmError("bad request")
    assert err.retryable is False
    assert err.status_code is None


def test_role_and_finish_reason_are_string_enums() -> None:
    """Both enums serialize to their string values for JSON-friendly logging."""
    assert Role.USER.value == "user"
    assert FinishReason.STOP.value == "stop"
    assert FinishReason("stop") == FinishReason.STOP
