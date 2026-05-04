"""Per-phase LLM client factory.

Holds the loaded `AgentCodeConfig` and produces an `LlmClient` for each phase
on demand. Clients are cached per phase so phases that fire many calls do
not pay the connection setup cost on every call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from llm.openai_compat import OpenAICompatClient

if TYPE_CHECKING:
    import httpx

    from config_loader import AgentCodeConfig
    from llm.base import LlmClient
    from llm.retry import RetryPolicy


class PhaseLlmFactory:
    """Build (and cache) a `LlmClient` per phase declared in the config."""

    __slots__ = ("_cache", "_config", "_retry", "_transport")

    def __init__(
        self,
        config: AgentCodeConfig,
        *,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._retry = retry
        self._transport = transport
        self._cache: dict[str, LlmClient] = {}

    def for_phase(self, name: str) -> LlmClient:
        """Return the cached client for `name`, building one if needed.

        Raises `KeyError` if `name` is not a declared phase.
        """
        if name in self._cache:
            return self._cache[name]
        phase_config = self._config.phases[name]
        client = OpenAICompatClient(
            phase_config,
            retry=self._retry,
            transport=self._transport,
        )
        self._cache[name] = client
        return client

    async def aclose(self) -> None:
        """Close every cached client."""
        for client in self._cache.values():
            await client.aclose()
        self._cache.clear()
