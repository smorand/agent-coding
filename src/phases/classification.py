"""Classification phase (FR-003).

Detects the project type from the workspace layout. In MVP only Python (and
empty workspaces, which are bootstrap candidates) are supported; non-Python
detections halt the pipeline with a clear error so the operator can decide
how to proceed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from phases.project_detector import (
    SUPPORTED_TYPES,
    DetectionResult,
    detect_project_type,
)
from state import PhaseName

logger = logging.getLogger(__name__)

CLASSIFICATION_REPORT_FILENAME = "classification.json"


class ClassificationPhase(Phase):
    """Confirm the project is Python (MVP scope) or an empty bootstrap target."""

    name = PhaseName.CLASSIFICATION

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Inspect the workspace, persist the result, halt on unsupported types."""
        workspace = ctx.work_dir.parent.parent  # .agent_work/<id>/ -> workspace root
        ticket_path = Path(ctx.ticket_path)
        result = await asyncio.to_thread(detect_project_type, workspace, ticket_path=ticket_path)
        await asyncio.to_thread(self._persist_report, ctx.work_dir, result)
        if result.is_supported:
            logger.info(
                "classification.run: %s (markers=%s)",
                result.project_type.value,
                list(result.markers),
            )
            return PhaseOutcome()
        logger.error(
            "classification.run: unsupported project type %s (markers=%s)",
            result.project_type.value,
            list(result.markers),
        )
        message = _build_unsupported_message(result)
        return PhaseOutcome(kind=OutcomeKind.HALT_ERROR, message=message)

    @staticmethod
    def _persist_report(work_dir: Path, result: DetectionResult) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / CLASSIFICATION_REPORT_FILENAME
        payload = {
            "project_type": result.project_type.value,
            "markers": list(result.markers),
            "is_supported": result.is_supported,
            "supported_types": sorted(t.value for t in SUPPORTED_TYPES),
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_unsupported_message(result: DetectionResult) -> str:
    if result.markers:
        markers = ", ".join(result.markers)
        return (
            f"Detected project type {result.project_type.value!r} (markers: {markers}). "
            f"Only {sorted(t.value for t in SUPPORTED_TYPES)} are supported in this MVP."
        )
    return (
        f"Could not determine project type (no Python markers like pyproject.toml found, "
        f"workspace is not empty). Only {sorted(t.value for t in SUPPORTED_TYPES)} are "
        "supported in this MVP."
    )
