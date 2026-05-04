"""Planning phase (skeleton).

Produces plan.md, todo.md, infra_needs.md under .agent_work/<ticket_id>/.
Implementation pending; this is the wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class PlanningPhase(Phase):
    """Plan the implementation and declare infra needs (FR-006)."""

    name = PhaseName.PLANNING

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real planning is FR-006."""
        logger.info("planning.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
