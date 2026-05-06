"""Pipeline orchestrator.

Executes the eight phases in order, persists the run state to
`.agent_work/<ticket_id>/state.json` after every phase transition, supports
resuming from a checkpoint when the same ticket is re-invoked, and (FR-011
business rule) re-runs the implementation phase ONCE when the reviewer
returns REQUEST_CHANGES with blocking concerns. After every phase
completion the orchestrator commits `.agent_work/<ticket_id>/state.json`
with the canonical message `agent-code: phase <name>` (FR-017 audit
trail), giving the branch one commit per phase boundary.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases import PIPELINE
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName, PhaseRecord, PhaseStatus, RunStatus, State, StateStore
from tools.anti_cheat import AntiCheatGuard
from tools.runner import AsyncSubprocessRunner
from tracing import trace_span

if TYPE_CHECKING:
    from pathlib import Path

    from tools.base import SubprocessRunner
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

AGENT_WORK_DIRNAME = ".agent_work"

EXIT_OK = 0
EXIT_DOR_FAILED = 1
EXIT_EXHAUSTED = 2
EXIT_SYSTEM_ERROR = 3

REVIEW_REPORT_FILENAME = "review.json"
MAX_REVIEW_RERUNS = 1  # FR-011: at most one re-run of implementation+review
AUDIT_COMMIT_TEMPLATE = "agent-code: phase {phase}"


class Orchestrator:
    """Drive a ticket through the seven-phase pipeline.

    The orchestrator is constructed with a workspace root and an optional
    custom phase tuple (for testing). On `run`, it materializes the
    `.agent_work/<ticket_id>/` directory, loads or creates the run state, and
    executes phases in order, persisting state after each phase transition.

    A `tools` argument can be either a `ToolRegistry` (the orchestrator wraps
    it in an `AntiCheatGuard`) or an `AntiCheatGuard` directly. The guard's
    `set_phase` is called before every phase and reset to None after the run
    completes (success, halt, or exception).
    """

    __slots__ = (
        "_audit_runner",
        "_audit_trail",
        "_guard",
        "_phases",
        "_template_version",
        "_workspace",
    )

    def __init__(
        self,
        workspace: Path,
        template_version: str,
        phases: tuple[Phase, ...] = PIPELINE,
        *,
        tools: ToolRegistry | AntiCheatGuard | None = None,
        audit_runner: SubprocessRunner | None = None,
        audit_trail: bool = True,
    ) -> None:
        self._workspace = workspace
        self._template_version = template_version
        self._phases = phases
        self._guard: AntiCheatGuard | None = None
        if tools is not None:
            self._guard = tools if isinstance(tools, AntiCheatGuard) else AntiCheatGuard(tools)
        self._audit_runner = audit_runner
        self._audit_trail = audit_trail

    @property
    def phases(self) -> tuple[Phase, ...]:
        """The phase tuple this orchestrator will execute."""
        return self._phases

    @property
    def guard(self) -> AntiCheatGuard | None:
        """The anti-cheat guard wrapping the tool registry, or None if no tools were provided."""
        return self._guard

    async def run(self, ticket_id: str, ticket_path: str) -> int:
        """Run the pipeline for one ticket and return the process exit code."""
        with trace_span("orchestrator.run", attributes={"ticket.id": ticket_id}):
            work_dir = self._workspace / AGENT_WORK_DIRNAME / ticket_id
            store = StateStore(work_dir)
            state = await self._load_or_create(store, ticket_id)
            try:
                return await self._execute(state, store, work_dir, ticket_path)
            finally:
                if self._guard is not None:
                    self._guard.set_phase(None)

    async def _load_or_create(self, store: StateStore, ticket_id: str) -> State:
        if store.exists():
            state = await store.load()
            logger.info(
                "Resuming ticket %s from phase %s (last checkpoint %s)",
                state.ticket_id,
                state.current_phase.value,
                state.last_checkpoint_at.isoformat(),
            )
            return state
        now = datetime.now(UTC)
        state = State(
            ticket_id=ticket_id,
            template_version=self._template_version,
            started_at=now,
            last_checkpoint_at=now,
            current_phase=self._phases[0].name,
            phases=[PhaseRecord(name=phase.name) for phase in self._phases],
        )
        await store.save(state)
        logger.info("Starting fresh run for ticket %s", ticket_id)
        return state

    async def _execute(
        self,
        state: State,
        store: StateStore,
        work_dir: Path,
        ticket_path: str,
    ) -> int:
        # The phase walk is wrapped in a small loop: when the reviewer returns
        # REQUEST_CHANGES with iteration < MAX_REVIEW_ITERATIONS we reset the
        # implementation + review records to PENDING and walk the remaining
        # tail again (FR-011 re-run business rule).
        while True:
            for phase in self._phases:
                record = self._record_for(state, phase.name)
                if record.status == PhaseStatus.COMPLETED:
                    logger.debug("Skipping completed phase %s", phase.name.value)
                    continue
                outcome = await self._run_one(phase, state, store, work_dir, ticket_path, record)
                await self._maybe_audit_commit(work_dir, phase.name)
                exit_code = self._exit_code_from(outcome)
                if exit_code is not None:
                    state.run_status = self._run_status_from(outcome)
                    state.exit_code = exit_code
                    await store.save(state)
                    return exit_code
                if phase.name == PhaseName.REVIEW and self._should_rerun_after_review(state):
                    logger.info(
                        "review iteration %d returned REQUEST_CHANGES; resetting implementation+review for one re-run",
                        state.review_iteration,
                    )
                    self._prepare_review_rerun(state, work_dir)
                    await store.save(state)
                    break  # re-enter the while loop
            else:
                # The for loop exhausted without break -> all phases COMPLETED.
                state.run_status = RunStatus.COMPLETED
                state.exit_code = EXIT_OK
                await store.save(state)
                return EXIT_OK

    def _should_rerun_after_review(self, state: State) -> bool:
        if state.review_verdict != "REQUEST_CHANGES":
            return False
        return state.review_iteration < MAX_REVIEW_RERUNS

    def _prepare_review_rerun(self, state: State, work_dir: Path) -> None:
        """Reset implementation+review records and stash the blocking concerns."""
        state.review_iteration += 1
        state.review_concerns = self._read_blocking_concerns(work_dir)
        for record in state.phases:
            if record.name in {PhaseName.IMPLEMENTATION, PhaseName.REVIEW}:
                record.status = PhaseStatus.PENDING
                record.started_at = None
                record.completed_at = None
                record.error = None

    @staticmethod
    def _read_blocking_concerns(work_dir: Path) -> str:
        path = work_dir / REVIEW_REPORT_FILENAME
        if not path.exists():
            return ""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        blocking = payload.get("blocking") or []
        if not blocking:
            return ""
        lines = [f"- {b.get('path', '?')}:{b.get('line', '?')} - {b.get('reason', '')}" for b in blocking]
        return "\n".join(lines)

    async def _maybe_audit_commit(self, work_dir: Path, phase: PhaseName) -> None:
        """Commit `.agent_work/<id>/state.json` after the phase if audit trail is on."""
        if not self._audit_trail:
            return
        runner: SubprocessRunner = self._audit_runner or AsyncSubprocessRunner()
        relative_state = work_dir.relative_to(self._workspace) / "state.json"
        # Stage just the state file. If the workspace isn't a git repo, this
        # silently fails and the orchestrator carries on.
        add = await runner.run(
            ["git", "add", str(relative_state)],
            cwd=self._workspace,
        )
        if add.returncode != 0:
            logger.debug("audit-trail: git add skipped (%s)", add.stderr.strip())
            return
        message = AUDIT_COMMIT_TEMPLATE.format(phase=phase.value)
        commit = await runner.run(
            ["git", "commit", "--allow-empty", "-m", message],
            cwd=self._workspace,
        )
        if commit.returncode != 0:
            logger.debug("audit-trail: git commit skipped (%s)", commit.stderr.strip())

    async def _run_one(
        self,
        phase: Phase,
        state: State,
        store: StateStore,
        work_dir: Path,
        ticket_path: str,
        record: PhaseRecord,
    ) -> PhaseOutcome:
        ctx = PhaseContext(
            state=state,
            work_dir=work_dir,
            ticket_path=ticket_path,
            tools=self._guard,
        )
        record.status = PhaseStatus.RUNNING
        record.started_at = datetime.now(UTC)
        state.current_phase = phase.name
        if self._guard is not None:
            self._guard.set_phase(phase.name)
        await store.save(state)
        with trace_span(f"phase.{phase.name.value}"):
            try:
                await phase.prepare(ctx)
                outcome = await phase.run(ctx)
                await phase.checkpoint(ctx)
            except Exception as exc:
                record.status = PhaseStatus.FAILED
                record.completed_at = datetime.now(UTC)
                record.error = repr(exc)
                await store.save(state)
                raise
        record.status = PhaseStatus.COMPLETED
        record.completed_at = datetime.now(UTC)
        await store.save(state)
        return outcome

    def _exit_code_from(self, outcome: PhaseOutcome) -> int | None:
        match outcome.kind:
            case OutcomeKind.CONTINUE:
                return None
            case OutcomeKind.HALT_OK:
                return EXIT_OK
            case OutcomeKind.HALT_DOR_FAILED:
                return EXIT_DOR_FAILED
            case OutcomeKind.HALT_EXHAUSTED:
                return EXIT_EXHAUSTED
            case OutcomeKind.HALT_ERROR:
                return EXIT_SYSTEM_ERROR

    def _record_for(self, state: State, name: PhaseName) -> PhaseRecord:
        for record in state.phases:
            if record.name == name:
                return record
        msg = f"State has no record for phase {name.value}"
        raise ValueError(msg)

    def _run_status_from(self, outcome: PhaseOutcome) -> RunStatus:
        match outcome.kind:
            case OutcomeKind.HALT_DOR_FAILED:
                return RunStatus.DOR_FAILED
            case OutcomeKind.HALT_EXHAUSTED:
                return RunStatus.EXHAUSTED
            case OutcomeKind.HALT_ERROR:
                return RunStatus.SYSTEM_ERROR
            case OutcomeKind.HALT_OK:
                return RunStatus.COMPLETED
            case OutcomeKind.CONTINUE:
                return RunStatus.RUNNING
