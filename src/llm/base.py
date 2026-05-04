"""LLM client contract and shared value objects.

Phases consume a `LlmClient` and never see the underlying transport. Inputs
and outputs are immutable value objects to keep the interface easy to mock
and reason about.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class Role(StrEnum):
    """Chat message author roles, OpenAI-style."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Why generation stopped, OpenAI-style."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChatMessage:
    """One turn in a chat exchange."""

    role: Role
    content: str


@dataclass(frozen=True)
class TokenUsage:
    """Token accounting for a single LLM call."""

    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ChatResponse:
    """The model's reply along with usage metadata."""

    content: str
    usage: TokenUsage
    model: str
    finish_reason: FinishReason
    duration_ms: float


class LlmError(Exception):
    """Raised when an LLM call fails irrecoverably (after retries)."""

    __slots__ = ("retryable", "status_code")

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LlmClient(ABC):
    """Abstract chat-completion client. Implementations target one endpoint."""

    @abstractmethod
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Send `messages` to the model and return the response.

        Implementations MUST emit an OpenTelemetry span named `llm.complete`
        with model, input_tokens, output_tokens, duration_ms attributes; they
        MUST NOT include the prompt or response content as span attributes.

        Raises `LlmError` after the configured retry policy is exhausted.
        """

    @abstractmethod
    async def aclose(self) -> None:
        """Release any underlying resources (HTTP connections, etc.)."""
