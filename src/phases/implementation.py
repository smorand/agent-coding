"""Implementation loop phase (skeleton).

Edits source files, runs `make check`, reads failures, adjusts. Tracks
iteration and approach counters with stop conditions (FR-008). Writes to
tests/test_*.py are blocked at the tool wrapper level. Implementation
pending; this is the wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class ImplementationPhase(Phase):
    """Implement the code that makes the E2E tests pass (FR-008, FR-009, FR-010)."""

    name = PhaseName.IMPLEMENTATION

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real loop is FR-008/009/010."""
        logger.info("implementation.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
