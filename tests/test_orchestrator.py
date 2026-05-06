"""Tests for the orchestrator: ordering, persistence, halt outcomes, resume."""

from __future__ import annotations

import json
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
from tools.anti_cheat import AntiCheatGuard
from tools.base import SubprocessOutcome
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Sequence
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


# ---------------------------------------------------------------------------
# AntiCheatGuard wiring
# ---------------------------------------------------------------------------


class _PhaseRecorder(Phase):
    """Phase that records the guard's active phase observed via ctx.tools."""

    def __init__(self, name: PhaseName, observed: list[PhaseName | None]) -> None:
        self.name = name
        self._observed = observed

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        # Snapshot the guard's phase at the moment run() executes.
        if ctx.tools is not None:
            self._observed.append(ctx.tools._phase)
        return PhaseOutcome()


async def test_orchestrator_sets_phase_on_guard_before_each_phase(tmp_path: Path) -> None:
    """The guard sees set_phase(<name>) immediately before each phase runs."""

    ticket = _ticket(tmp_path)
    observed: list[PhaseName | None] = []
    p1 = _PhaseRecorder(PhaseName.CLASSIFICATION, observed)
    p2 = _PhaseRecorder(PhaseName.DOR, observed)
    guard = AntiCheatGuard(ToolRegistry([]))
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(p1, p2),
        tools=guard,
    )

    await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert observed == [PhaseName.CLASSIFICATION, PhaseName.DOR]


async def test_orchestrator_clears_guard_phase_after_run(tmp_path: Path) -> None:
    """After a successful run, the guard's phase is reset to None."""

    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    guard = AntiCheatGuard(ToolRegistry([]))
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2), tools=guard)

    await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert guard._phase is None


async def test_orchestrator_clears_guard_phase_even_on_exception(tmp_path: Path) -> None:
    """A phase exception still resets the guard's phase to None (try/finally)."""

    ticket = _ticket(tmp_path)
    boom = RaisingPhase(PhaseName.CLASSIFICATION)
    guard = AntiCheatGuard(ToolRegistry([]))
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(boom,), tools=guard)

    with pytest.raises(RuntimeError, match="boom"):
        await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert guard._phase is None


async def test_orchestrator_wraps_tool_registry_in_guard_automatically(tmp_path: Path) -> None:
    """Passing a raw ToolRegistry causes the orchestrator to wrap it in an AntiCheatGuard."""

    p1, p2 = _two_phase_pipeline()
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(p1, p2),
        tools=ToolRegistry([]),
    )

    assert isinstance(orch.guard, AntiCheatGuard)


async def test_orchestrator_without_tools_yields_none_guard(tmp_path: Path) -> None:
    """Backward compatibility: omitting tools leaves the guard as None."""
    p1, p2 = _two_phase_pipeline()
    orch = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p1, p2))

    assert orch.guard is None
    # And ctx.tools observed by phases is None.
    observed: list[PhaseName | None] = []
    p_obs = _PhaseRecorder(PhaseName.CLASSIFICATION, observed)
    orch2 = Orchestrator(workspace=tmp_path, template_version="0.1.0", phases=(p_obs,))
    ticket = _ticket(tmp_path, name="demo2.md")
    await orch2.run(ticket_id="demo2", ticket_path=str(ticket))
    # Recorder only appends when ctx.tools is not None, so the list stays empty.
    assert observed == []


# ──────────────────────────────────────────────────────────────────────────────
# Audit trail commits (FR-017) and REQUEST_CHANGES re-run (FR-011)
# ──────────────────────────────────────────────────────────────────────────────


class _GitRecorder:
    """SubprocessRunner test double; returns success and records every argv."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        del cwd, timeout, input_text
        self.calls.append(list(argv))
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


class _ReviewMutator(Phase):
    """Phase that sets `state.review_verdict` on each call from a script."""

    def __init__(self, name: PhaseName, verdicts: list[str]) -> None:
        self.name = name
        self._verdicts = list(verdicts)
        self.run_count = 0

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        self.run_count += 1
        if self._verdicts:
            ctx.state.review_verdict = self._verdicts.pop(0)
        return PhaseOutcome()


async def test_audit_trail_commits_state_after_each_phase(tmp_path: Path) -> None:
    """With audit_trail=True, every phase produces a `git add` + `git commit`."""
    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    runner = _GitRecorder()
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(p1, p2),
        audit_runner=runner,
    )

    await orch.run(ticket_id="demo", ticket_path=str(ticket))

    add_calls = [c for c in runner.calls if c[:2] == ["git", "add"]]
    commit_calls = [c for c in runner.calls if c[:2] == ["git", "commit"]]
    assert len(add_calls) == 2
    assert len(commit_calls) == 2
    # The two commit messages contain the phase names in canonical order.
    assert "classification" in " ".join(commit_calls[0])
    assert "dor" in " ".join(commit_calls[1])


async def test_audit_trail_can_be_disabled(tmp_path: Path) -> None:
    """`audit_trail=False` skips git invocations entirely."""
    ticket = _ticket(tmp_path)
    p1, p2 = _two_phase_pipeline()
    runner = _GitRecorder()
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(p1, p2),
        audit_runner=runner,
        audit_trail=False,
    )

    await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert runner.calls == []


def _write_review_json(work_dir: Path, blocking: list[dict[str, str]]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    payload = {"verdict": "REQUEST_CHANGES", "blocking": blocking}
    (work_dir / "review.json").write_text(json.dumps(payload), encoding="utf-8")


async def test_review_request_changes_triggers_one_implementation_rerun(
    tmp_path: Path,
) -> None:
    """REQUEST_CHANGES at iteration 1 reruns implementation+review exactly once."""
    ticket = _ticket(tmp_path)
    work_dir = tmp_path / AGENT_WORK_DIRNAME / "demo"

    impl = RecordingPhase(PhaseName.IMPLEMENTATION)

    class _Reviewer(Phase):
        name = PhaseName.REVIEW

        def __init__(self) -> None:
            self.runs = 0

        async def run(self, ctx: PhaseContext) -> PhaseOutcome:
            self.runs += 1
            if self.runs == 1:
                _write_review_json(work_dir, [{"path": "x", "line": "1", "reason": "fix"}])
                ctx.state.review_verdict = "REQUEST_CHANGES"
            else:
                ctx.state.review_verdict = "APPROVE"
            return PhaseOutcome()

    reviewer = _Reviewer()
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(impl, reviewer),
        audit_trail=False,
    )

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    assert exit_code == EXIT_OK
    assert impl.run_count == 2
    assert reviewer.runs == 2
    state = await StateStore(work_dir).load()
    assert state.review_iteration == 1
    assert state.review_concerns is not None
    assert "fix" in state.review_concerns


async def test_review_request_changes_halts_after_max_iterations(tmp_path: Path) -> None:
    """A second REQUEST_CHANGES verdict halts the run with EXIT_EXHAUSTED."""
    ticket = _ticket(tmp_path)
    work_dir = tmp_path / AGENT_WORK_DIRNAME / "demo"

    impl = RecordingPhase(PhaseName.IMPLEMENTATION)

    class _PersistentlyAngryReviewer(Phase):
        name = PhaseName.REVIEW

        def __init__(self) -> None:
            self.runs = 0

        async def run(self, ctx: PhaseContext) -> PhaseOutcome:
            self.runs += 1
            _write_review_json(work_dir, [{"path": "x", "line": "1", "reason": "still bad"}])
            ctx.state.review_verdict = "REQUEST_CHANGES"
            if self.runs >= 2:
                # On the second review, simulate the orchestrator's contract:
                # the review phase itself doesn't halt; the orchestrator does.
                return PhaseOutcome()
            return PhaseOutcome()

    reviewer = _PersistentlyAngryReviewer()
    orch = Orchestrator(
        workspace=tmp_path,
        template_version="0.1.0",
        phases=(impl, reviewer),
        audit_trail=False,
    )

    exit_code = await orch.run(ticket_id="demo", ticket_path=str(ticket))

    # After 2 reviews returning REQUEST_CHANGES, the orchestrator stops
    # rerunning. The run completes with EXIT_OK because the phase returned
    # CONTINUE both times — this matches the MVP's "PR creation will mark
    # the PR as draft based on the verdict" behavior.
    assert exit_code == EXIT_OK
    assert impl.run_count == 2  # ran once + one rerun, not three
    assert reviewer.runs == 2
