"""Retry policy with exponential backoff and jitter for LLM calls."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter.

    Delay before attempt N (0-indexed) is sampled uniformly in
    [0, min(max_delay, base_delay * factor**N)]. Calls are retried up to
    `max_attempts` times INCLUSIVE of the first attempt; so `max_attempts=3`
    means the original call plus up to two retries.
    """

    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    backoff_factor: float = 2.0


DEFAULT_RETRY = RetryPolicy()


async def sleep_for_backoff(
    attempt: int,
    policy: RetryPolicy,
    *,
    rng: random.Random | None = None,
) -> None:
    """Sleep before the next attempt according to `policy`.

    `attempt` is the index of the next attempt (1 means we just failed once
    and are about to retry; we sleep based on the failed attempt's backoff).
    `rng` lets tests inject a deterministic random source.
    """
    capped = min(policy.max_delay_seconds, policy.base_delay_seconds * policy.backoff_factor ** (attempt - 1))
    chooser = rng if rng is not None else random.SystemRandom()
    delay = chooser.uniform(0, capped)
    logger.debug("Retry attempt %s sleeping %.2fs (cap %.2fs)", attempt, delay, capped)
    await asyncio.sleep(delay)
