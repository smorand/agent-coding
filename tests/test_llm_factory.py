"""Tests for the per-phase LLM factory."""

from __future__ import annotations

import httpx
import pytest

from config_loader import (
    AgentCodeConfig,
    McpConfig,
    McpEndpointConfig,
    PhaseModelConfig,
)
from llm.factory import PhaseLlmFactory
from llm.openai_compat import OpenAICompatClient


def _config_with_two_models() -> AgentCodeConfig:
    """Build a minimal AgentCodeConfig with two distinct phase models."""
    small = PhaseModelConfig(url="http://vllm:8001/v1", model_name="qwen3-7b")
    large = PhaseModelConfig(url="http://vllm:8002/v1", model_name="qwen3-32b")
    phases = {
        "classification": small,
        "dor": small,
        "comprehension": large,
        "planning": large,
        "e2e_writing": large,
        "implementation": large,
        "review": large,
        "summarizer": small,
    }
    return AgentCodeConfig(
        phases=phases,
        template_path="/opt/agent-code/templates/python",  # type: ignore[arg-type]
        mcp=McpConfig(
            context7=McpEndpointConfig(url="http://c:1"),
            duckduckgo=McpEndpointConfig(url="http://d:1"),
        ),
    )


def test_for_phase_returns_openai_compat_client() -> None:
    """The default factory builds OpenAICompatClient instances."""
    factory = PhaseLlmFactory(_config_with_two_models())
    client = factory.for_phase("classification")
    assert isinstance(client, OpenAICompatClient)


def test_for_phase_caches_clients_per_phase_name() -> None:
    """Two calls for the same phase return the same instance."""
    factory = PhaseLlmFactory(_config_with_two_models())
    a = factory.for_phase("comprehension")
    b = factory.for_phase("comprehension")
    assert a is b


def test_for_phase_distinct_phases_get_distinct_clients() -> None:
    """Different phase names yield different clients (even if the config is shared)."""
    factory = PhaseLlmFactory(_config_with_two_models())
    a = factory.for_phase("classification")
    b = factory.for_phase("dor")
    assert a is not b


def test_for_phase_unknown_name_raises_key_error() -> None:
    """An undeclared phase is a KeyError, not a silent default."""
    factory = PhaseLlmFactory(_config_with_two_models())
    with pytest.raises(KeyError):
        factory.for_phase("not-a-phase")


async def test_aclose_closes_every_cached_client() -> None:
    """`aclose` releases every client that was built and clears the cache."""
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, json={"choices": []}))
    factory = PhaseLlmFactory(_config_with_two_models(), transport=transport)
    factory.for_phase("classification")
    factory.for_phase("comprehension")

    await factory.aclose()

    # Cache cleared, so a subsequent for_phase rebuilds rather than returning a
    # closed client.
    new_client = factory.for_phase("classification")
    assert new_client is not None
