"""Comprehension phase (skeleton).

Reads CLAUDE.md, .agent_docs/, and navigates the codebase via grep, find,
ast-grep, LSP. Produces context for the planning phase. Implementation
pending; this is the wiring stub.
"""

from __future__ import annotations

import logging

from phases.base import Phase, PhaseContext, PhaseOutcome
from state import PhaseName

logger = logging.getLogger(__name__)


class ComprehensionPhase(Phase):
    """Build comprehension context from the codebase and ticket (FR-005)."""

    name = PhaseName.COMPREHENSION

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Skeleton: log and continue. Real comprehension is FR-005."""
        logger.info("comprehension.run: skeleton, ticket=%s", ctx.ticket_path)
        return PhaseOutcome()
