"""Reviewer phase (skeleton).

Runs with a fresh model context to verify code quality, doc freshness, PR
template compliance. Implementation pending; this is the wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class ReviewPhase(Phase):
    """Review the implementation result with a fresh-context agent (FR-011)."""

    name = PhaseName.REVIEW

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real review is FR-011."""
        logger.info("review.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
