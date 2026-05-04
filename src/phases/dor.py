"""Definition of Ready (DoR) phase (skeleton).

Validates the ticket against the canonical template (Appendix A of the spec,
implemented in vars/ticket-template/). On failure, the orchestrator stops
with a `HALT_DOR_FAILED` outcome. Implementation pending; this is the wiring
stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class DorPhase(Phase):
    """Validate the ticket structure against the DoR rules (FR-004)."""

    name = PhaseName.DOR

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real DoR validation is FR-004."""
        logger.info("dor.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
