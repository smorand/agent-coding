"""Classification phase (FR-003) with optional bootstrap (FR-014).

Detects the project type from the workspace layout. In MVP only Python (and
empty workspaces, which become bootstrap candidates) are supported. When the
workspace is empty AND a `template_path` was provided to the phase, the
canonical Python template is materialized in place and a re-detection
confirms the project is now Python before continuing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from bootstrap import (
    BootstrapError,
    BootstrapResult,
    extract_inputs_from_ticket,
    materialize_template,
)
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from phases.project_detector import (
    SUPPORTED_TYPES,
    DetectionResult,
    ProjectType,
    detect_project_type,
)
from state import PhaseName

logger = logging.getLogger(__name__)

CLASSIFICATION_REPORT_FILENAME = "classification.json"


class ClassificationPhase(Phase):
    """Confirm the project is Python (MVP scope) or bootstrap an empty workspace."""

    name = PhaseName.CLASSIFICATION

    __slots__ = ("_template_path",)

    def __init__(self, template_path: Path | None = None) -> None:
        self._template_path = template_path

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Inspect the workspace, optionally bootstrap, persist the result."""
        workspace = ctx.work_dir.parent.parent
        ticket_path = Path(ctx.ticket_path)
        result = await asyncio.to_thread(detect_project_type, workspace, ticket_path=ticket_path)
        bootstrap_result: BootstrapResult | None = None
        if result.project_type == ProjectType.EMPTY and self._template_path is not None:
            try:
                bootstrap_result = await asyncio.to_thread(self._bootstrap, workspace, ticket_path)
            except BootstrapError as exc:
                await asyncio.to_thread(self._persist_report, ctx.work_dir, result, None, str(exc))
                return PhaseOutcome(kind=OutcomeKind.HALT_ERROR, message=str(exc))
            # Re-detect: the workspace should now be PYTHON.
            result = await asyncio.to_thread(detect_project_type, workspace, ticket_path=ticket_path)
        await asyncio.to_thread(self._persist_report, ctx.work_dir, result, bootstrap_result, None)
        if result.is_supported and result.project_type == ProjectType.PYTHON:
            logger.info(
                "classification.run: %s (markers=%s%s)",
                result.project_type.value,
                list(result.markers),
                ", bootstrapped" if bootstrap_result else "",
            )
            return PhaseOutcome()
        if result.is_supported and result.project_type == ProjectType.EMPTY:
            message = (
                "Workspace is empty and no template_path was configured for bootstrap. "
                "Configure `template_path` in config.yaml or pre-populate the workspace."
            )
            logger.error("classification.run: %s", message)
            return PhaseOutcome(kind=OutcomeKind.HALT_ERROR, message=message)
        logger.error(
            "classification.run: unsupported project type %s (markers=%s)",
            result.project_type.value,
            list(result.markers),
        )
        return PhaseOutcome(kind=OutcomeKind.HALT_ERROR, message=_build_unsupported_message(result))

    def _bootstrap(self, workspace: Path, ticket_path: Path) -> BootstrapResult:
        if self._template_path is None:  # pragma: no cover - guarded by caller
            msg = "Internal error: _bootstrap called without template_path"
            raise BootstrapError(msg)
        inputs = extract_inputs_from_ticket(ticket_path)
        result = materialize_template(workspace, self._template_path, inputs)
        logger.info(
            "Bootstrap completed: %d files materialized (template v%s)",
            len(result.materialized_files),
            result.template_version,
        )
        return result

    @staticmethod
    def _persist_report(
        work_dir: Path,
        result: DetectionResult,
        bootstrap_result: BootstrapResult | None,
        error: str | None,
    ) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / CLASSIFICATION_REPORT_FILENAME
        payload: dict[str, object] = {
            "project_type": result.project_type.value,
            "markers": list(result.markers),
            "is_supported": result.is_supported,
            "supported_types": sorted(t.value for t in SUPPORTED_TYPES),
        }
        if bootstrap_result is not None:
            payload["bootstrap"] = {
                "template_version": bootstrap_result.template_version,
                "materialized_files": list(bootstrap_result.materialized_files),
            }
        if error is not None:
            payload["error"] = error
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
