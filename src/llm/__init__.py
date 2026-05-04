"""LLM abstraction for agent-code.

Phases call into a `LlmClient` (interface defined in `llm.base`). The default
implementation is `OpenAICompatClient` which speaks any OpenAI-compatible
endpoint (vLLM, TGI, llama.cpp server, OpenAI itself). The `PhaseLlmFactory`
hands out per-phase clients backed by the loaded `AgentCodeConfig`.

OpenTelemetry spans are emitted around every call. Spans capture model name,
input and output token counts, duration, and error status; NEVER prompts or
responses.
"""

from __future__ import annotations

from llm.base import (
    ChatMessage,
    ChatResponse,
    FinishReason,
    LlmClient,
    LlmError,
    Role,
    TokenUsage,
)
from llm.factory import PhaseLlmFactory
from llm.openai_compat import OpenAICompatClient
from llm.retry import DEFAULT_RETRY, RetryPolicy

__all__ = [
    "DEFAULT_RETRY",
    "ChatMessage",
    "ChatResponse",
    "FinishReason",
    "LlmClient",
    "LlmError",
    "OpenAICompatClient",
    "PhaseLlmFactory",
    "RetryPolicy",
    "Role",
    "TokenUsage",
]
