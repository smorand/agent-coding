"""Tests for the exponential-backoff retry helper."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

from llm.retry import DEFAULT_RETRY, RetryPolicy, sleep_for_backoff

if TYPE_CHECKING:
    import pytest


def test_default_policy_has_three_attempts() -> None:
    """The default policy retries up to two times after the first try."""
    assert DEFAULT_RETRY.max_attempts == 3


async def test_sleep_for_backoff_respects_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sleep is bounded by `max_delay_seconds` no matter the attempt index."""
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    policy = RetryPolicy(
        max_attempts=10,
        base_delay_seconds=1.0,
        max_delay_seconds=5.0,
        backoff_factor=2.0,
    )
    rng = random.Random(0)

    await sleep_for_backoff(attempt=10, policy=policy, rng=rng)

    assert captured, "sleep was not invoked"
    assert 0 <= captured[0] <= 5.0


async def test_sleep_for_backoff_grows_with_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """With factor>1 and no cap, the upper bound on delay grows with attempt."""
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=1.0,
        max_delay_seconds=1000.0,
        backoff_factor=2.0,
    )

    # Use a deterministic RNG that always returns the upper bound.
    class _MaxRng:
        def uniform(self, _a: float, b: float) -> float:
            return b

    await sleep_for_backoff(attempt=1, policy=policy, rng=_MaxRng())  # type: ignore[arg-type]
    await sleep_for_backoff(attempt=2, policy=policy, rng=_MaxRng())  # type: ignore[arg-type]
    await sleep_for_backoff(attempt=3, policy=policy, rng=_MaxRng())  # type: ignore[arg-type]

    assert captured == [1.0, 2.0, 4.0]


async def test_sleep_uses_system_random_when_rng_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `rng` is None, sleep_for_backoff still completes without error."""
    captured: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await sleep_for_backoff(attempt=1, policy=DEFAULT_RETRY)

    assert captured
