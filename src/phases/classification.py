"""Classification phase (skeleton).

Detects the project type from the ticket and the existing project layout. In
the MVP only Python is supported, so this phase will essentially confirm or
reject. Implementation pending; this is the orchestrator wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class ClassificationPhase(Phase):
    """Confirm the project is Python (MVP scope)."""

    name = PhaseName.CLASSIFICATION

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real classification is FR-003."""
        logger.info("classification.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
