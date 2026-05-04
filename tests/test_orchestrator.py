"""Tests for the orchestrator: ordering, persistence, halt outcomes, resume."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from orchestrator import (
    AGENT_WORK_DIRNAME,
    EXIT_DOR_FAILED,
    EXIT_EXHAUSTED,
    EXIT_OK,
    EXIT_SYSTEM_ERROR,
    Orchestrator,
)
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName, PhaseStatus, RunStatus, StateStore

if TYPE_CHECKING:
    from pathlib import Path


class RecordingPhase(Phase):
    """Test phase that records calls and returns a configurable outcome."""

    def __init__(self, name: PhaseName, outcome: PhaseOutcome | None = None) -> None:
        self.name = name
        self._outcome = outcome or PhaseOutcome()
        self.run_count = 0

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        self.run_count += 1
        return self._outcome


class RaisingPhase(Phase):
    """Test phase that raises during run to verify error handling."""

    def __init__(self, name: PhaseName) -> None:
        self.name = name

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        msg = "boom"
        raise RuntimeError(msg)


def _ticket(tmp_path: Path, name: str = "demo.md") -> Path:
    path = tmp_path / name
    path.write_text("# demo\n", encoding="utf-8")
    return path


def _two_phase_pipeline() -> tuple[RecordingPhase, RecordingPhase]:
    return RecordingPhase(PhaseName.CLASSIFICATION), RecordingPhase(PhaseName.DOR)


async def test_runs_all_phases_in_order(tmp_path: Path) -> None:
    """Every phase runs exactly once when all return CONTINUE."""
    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2))

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_OK
    assert p1.run_count == 1
    assert p2.run_count == 1


async def test_persists_state_after_each_phase(tmp_path: Path) -> None:
    """State.json reflects every phase as COMPLETED at the end of a green run."""
    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2))

    await orch.run(ticket_id="demo", ticket_path=str(ticket))

    work_dir = tmp_path / AGENT_WORK_DIRNAME / "demo"
    state = await StateStore(work_dir).load()
    assert state.run_status == RunStatus.COMPLETED
    assert state.exit_code == EXIT_OK
    assert all(record.status == PhaseStatus.COMPLETED for record in state.phases)


async def test_halt_dor_failed_stops_and_returns_exit_one(tmp_path: Path) -> None:
    """A phase returning HALT_DOR_FAILED short-circuits with exit code 1."""
    ticket = _ticket(tmp_path)
    p1 = RecordingPhase(PhaseName.CLASSIFICATION)
    p2 = RecordingPhase(PhaseName.DOR, outcome=PhaseOutcome(kind=OutcomeKind.HALT_DOR_FAILED))
    p3 = RecordingPhase(PhaseName.COMPREHENSION)
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2, p3))

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_DOR_FAILED
    assert p1.run_count == 1
    assert p2.run_count == 1
    assert p3.run_count == 0
    state = await StateStore(tmp_path / AGENT_WORK_DIRNAME / "demo").load()
    assert state.run_status == RunStatus.DOR_FAILED


async def test_halt_exhausted_returns_exit_two(tmp_path: Path) -> None:
    """A phase returning HALT_EXHAUSTED yields exit code 2."""
    ticket = _ticket(tmp_path)
    p1 = RecordingPhase(
        PhaseName.CLASSIFICATION,
        outcome=PhaseOutcome(kind=OutcomeKind.HALT_EXHAUSTED),
    )
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1,))

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_EXHAUSTED
    state = await StateStore(tmp_path / AGENT_WORK_DIRNAME / "demo").load()
    assert state.run_status == RunStatus.EXHAUSTED


async def test_resume_skips_completed_phases(tmp_path: Path) -> None:
    """When a state file marks a phase COMPLETED, a new run does not re-execute it."""
    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    orch_first = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2))
    await orch_first.run(ticket_id="demo", ticket_path=str(ticket))

    # Second invocation with the same ticket id; the state from the first run is on disk.
    p1_again, p2_again = _two_phase_pipeline()
    orch_resume = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1_again, p2_again))
    exit_code = await orch_resume.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_OK
    assert p1_again.run_count == 0
    assert p2_again.run_count == 0


async def test_phase_exception_marks_phase_failed_and_propagates(tmp_path: Path) -> None:
    """An exception during run sets the phase status to FAILED and re-raises."""
    ticket = _ticket(tmp_path)
    boom = RaisingPhase(PhaseName.CLASSIFICATION)
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(boom,))

    with pytest.raises(RuntimeError, match="boom"):
        await orch.run(ticket_id="demo", ticket_path=str(ticket))

    state = await StateStore(tmp_path / AGENT_WORK_DIRNAME / "demo").load()
    failed = next(record for record in state.phases if record.name == PhaseName.CLASSIFICATION)
    assert failed.status == PhaseStatus.FAILED
    assert failed.error is not None
    assert "boom" in failed.error


async def test_halt_error_returns_exit_three(tmp_path: Path) -> None:
    """HALT_ERROR yields exit code 3 and marks run as system_error."""
    ticket = _ticket(tmp_path)
    p1 = RecordingPhase(
        PhaseName.CLASSIFICATION,
        outcome=PhaseOutcome(kind=OutcomeKind.HALT_ERROR),
    )
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1,))

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_SYSTEM_ERROR
    state = await StateStore(tmp_path / AGENT_WORK_DIRNAME / "demo").load()
    assert state.run_status == RunStatus.SYSTEM_ERROR


async def test_halt_ok_returns_exit_zero_even_mid_pipeline(tmp_path: Path) -> None:
    """HALT_OK from a mid-pipeline phase is a clean stop with exit 0."""
    ticket = _ticket(tmp_path)
    p1 = RecordingPhase(
        PhaseName.CLASSIFICATION,
        outcome=PhaseOutcome(kind=OutcomeKind.HALT_OK),
    )
    p2 = RecordingPhase(PhaseName.DOR)
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2))

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_OK
    assert p2.run_count == 0
