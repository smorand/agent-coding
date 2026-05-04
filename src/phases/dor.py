"""Definition of Ready (DoR) phase (FR-004).

Validates the ticket against the canonical template (Appendix A of the spec,
implemented in vars/ticket-template/). On success, returns CONTINUE so the
orchestrator advances to comprehension. On failure, appends the canonical
DoR comment to the ticket file, persists the report under .agent_work/, and
returns HALT_DOR_FAILED so the orchestrator exits with code 1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from phases.dor_validator import (
    DorReport,
    DorStatus,
    format_dor_comment,
    validate_ticket,
)
from state import PhaseName

logger = logging.getLogger(__name__)

DOR_REPORT_FILENAME = "dor_report.json"
DEFAULT_AGENT_VERSION = "0.1.0"


class DorPhase(Phase):
    """Validate the ticket structure against the DoR rules (FR-004)."""

    name = PhaseName.DOR

    __slots__ = ("_agent_version",)

    def __init__(self, agent_version: str = DEFAULT_AGENT_VERSION) -> None:
        self._agent_version = agent_version

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Validate the ticket; on failure, append the comment and HALT."""
        ticket_path = Path(ctx.ticket_path)
        report = await asyncio.to_thread(validate_ticket, ticket_path)
        await asyncio.to_thread(self._persist_report, ctx.work_dir, report)
        if report.status == DorStatus.READY:
            logger.info("DoR check: READY (%s)", ticket_path)
            return PhaseOutcome()
        logger.warning("DoR check: NOT_READY for %s (%d issue(s))", ticket_path, len(report.issues))
        await asyncio.to_thread(self._append_comment, ticket_path, report)
        return PhaseOutcome(
            kind=OutcomeKind.HALT_DOR_FAILED,
            message=f"DoR rejected ticket: {len(report.issues)} issue(s)",
        )

    def _append_comment(self, ticket_path: Path, report: DorReport) -> None:
        comment = format_dor_comment(report, agent_version=self._agent_version)
        with ticket_path.open("a", encoding="utf-8") as fh:
            fh.write("\n")
            fh.write(comment)
        logger.debug("DoR comment appended to %s", ticket_path)

    @staticmethod
    def _persist_report(work_dir: Path, report: DorReport) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / DOR_REPORT_FILENAME
        payload = {
            "status": report.status.value,
            "generated_at": report.generated_at.isoformat(),
            "issues": [{"field": issue.field, "reason": issue.reason} for issue in report.issues],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
