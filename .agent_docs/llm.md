# LLM Abstraction

> Reference for the LLM client layer that every phase uses to talk to a model.
> Default implementation is `OpenAICompatClient` over httpx; the
> `PhaseLlmFactory` hands out one cached client per phase declared in the
> loaded `AgentCodeConfig`. Async-first, retry on transient failures, OTel
> spans without prompt or response content.

## Files

- `src/llm/base.py`: `LlmClient` ABC, value objects (`ChatMessage`, `ChatResponse`, `TokenUsage`), enums (`Role`, `FinishReason`), `LlmError`.
- `src/llm/retry.py`: `RetryPolicy` dataclass, `sleep_for_backoff` (exponential with full jitter), `DEFAULT_RETRY` constant.
- `src/llm/openai_compat.py`: `OpenAICompatClient` implementation against OpenAI-style `/chat/completions`.
- `src/llm/factory.py`: `PhaseLlmFactory` builds and caches one client per phase.
- `src/llm/__init__.py`: re-exports the public surface.

## Why an abstraction

Phases should not know which transport is in use. Today every endpoint we target speaks OpenAI-compatible HTTP (vLLM, Text Generation Inference, llama.cpp server, OpenAI itself). If we ever need a non-HTTP transport (in-process model, MLX, etc.), implementing `LlmClient` is the only required change.

## Public surface

```python
from llm import (
    PhaseLlmFactory,
    LlmClient,
    ChatMessage,
    ChatResponse,
    Role,
    FinishReason,
    LlmError,
    RetryPolicy,
    DEFAULT_RETRY,
)

factory = PhaseLlmFactory(config)
client = factory.for_phase("classification")
response = await client.complete(
    [ChatMessage(role=Role.USER, content="hello")],
    max_tokens=128,
)
print(response.content, response.usage.total_tokens)
await factory.aclose()
```

`PhaseLlmFactory` caches one client per phase. Different phases pointing at the same model URL still get different `OpenAICompatClient` instances (each owns its own httpx.AsyncClient), which keeps connection pools per-phase and isolates failures.

## Retry policy

`RetryPolicy(max_attempts=N, base_delay_seconds, max_delay_seconds, backoff_factor)` controls the loop:

- Retried on: HTTP 5xx, HTTP 429, `httpx.TimeoutException`, `httpx.TransportError` (connection refused, DNS failure, etc.).
- NOT retried on: 4xx other than 429.
- Backoff: full-jitter exponential. Delay before attempt N is uniform in `[0, min(max_delay, base * factor**(N-1))]`.

The default policy retries up to 3 attempts (initial + 2 retries), 1s base, 30s cap, factor 2.

`sleep_for_backoff` accepts an injected RNG so tests can verify deterministic delays.

## OpenTelemetry

Every `complete` call opens a `llm.complete` span with these attributes:

| Attribute | When set |
|---|---|
| `llm.model` | always |
| `llm.endpoint` | always |
| `llm.input_tokens` | on success |
| `llm.output_tokens` | on success |
| `llm.duration_ms` | on success |
| `llm.finish_reason` | on success |

**Never recorded**: prompts, responses, message content, API keys, headers. This is enforced by code (we only set the attributes listed above) and by the test suite (the OTel exporter is JSONL and the audit trail tests grep for forbidden content).

## API key handling

When `phase.api_key_env` is set in the config, the client reads the named environment variable at call time and sends `Authorization: Bearer <value>`. If the env var is unset or empty, the client raises `LlmError` BEFORE making the HTTP call (no leaked partial requests).

The variable name is the only thing recorded in the audit trail; the value is never written to disk by this module.

## Error model

All transport, parsing, and retry exhaustion errors surface as `LlmError`:

```python
class LlmError(Exception):
    retryable: bool
    status_code: int | None
```

Callers can branch on `status_code` for fine-grained handling. The orchestrator currently wraps any `LlmError` from a phase as a phase failure (`PhaseStatus.FAILED`) and propagates.

## Testing

The client takes a `transport: httpx.AsyncBaseTransport | None` parameter. Tests pass `httpx.MockTransport(handler)` to fully control responses without hitting the network. No `respx` or other mocking library is needed.

Test files:

- `tests/test_llm_base.py`: value object invariants and exception fields.
- `tests/test_llm_retry.py`: backoff math and jitter bounds.
- `tests/test_llm_openai_compat.py`: request shape, headers, retries on 5xx/429/network, no retry on 4xx, max-attempts exhaustion, malformed responses, override semantics.
- `tests/test_llm_factory.py`: per-phase caching, distinct phases get distinct clients, `aclose` clears the cache.

## Out of MVP scope

- Tool/function calling (the OpenAI-compat schema supports it, but the agent's phases do not yet emit tool calls).
- Streaming responses (today the client awaits the full response; streaming would change the OTel span layout).
- Custom retry policies for transient HTTP 408 (request timeout) and 502/504 (gateway issues): currently lumped with 5xx via the `>= 500` threshold, which is correct.
- Per-phase concurrency limits (semaphore around `complete`): deferred until a phase fires concurrent calls.
