"""End-to-end test writing phase (skeleton).

Writes E2E tests under tests/ from the ticket's acceptance criteria, then
locks them by recording the resulting commit SHA. Implementation pending;
this is the wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class E2eWritingPhase(Phase):
    """Write the E2E tests in isolation, then lock them (FR-007)."""

    name = PhaseName.E2E_WRITING

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real E2E writing is FR-007."""
        logger.info("e2e_writing.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
