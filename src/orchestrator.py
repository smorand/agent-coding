"""Pipeline orchestrator.

Executes the seven phases in order, persists the run state to
.agent_work/<ticket_id>/state.json after every phase transition, and supports
resuming from a checkpoint when the same ticket is re-invoked.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases import PIPELINE
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName, PhaseRecord, PhaseStatus, RunStatus, State, StateStore
from tracing import trace_span

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_WORK_DIRNAME = ".agent_work"

EXIT_OK = 0
EXIT_DOR_FAILED = 1
EXIT_EXHAUSTED = 2
EXIT_SYSTEM_ERROR = 3


class Orchestrator:
    """Drive a ticket through the seven-phase pipeline.

    The orchestrator is constructed with a workspace root and an optional
    custom phase tuple (for testing). On `run`, it materializes the
    `.agent_work/<ticket_id>/` directory, loads or creates the run state, and
    executes phases in order, persisting state after each phase transition.
    """

    __slots__ = ("_phases", "_template_version", "_workspace")

    def __init__(
        self,
        workspace: Path,
        template_version: str,
        phases: tuple[Phase, ...] = PIPELINE,
    ) -> None:
        self._workspace = workspace
        self._template_version = template_version
        self._phases = phases

    @property
    def phases(self) -> tuple[Phase, ...]:
        """The phase tuple this orchestrator will execute."""
        return self._phases

    async def run(self, ticket_id: str, ticket_path: str) -> int:
        """Run the pipeline for one ticket and return the process exit code."""
        with trace_span("orchestrator.run", attributes={"ticket.id": ticket_id}):
            work_dir = self._workspace / AGENT_WORK_DIRNAME / ticket_id
            store = StateStore(work_dir)
            state = await self._load_or_create(store, ticket_id)
            return await self._execute(state, store, work_dir, ticket_path)

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
        for phase in self._phases:
            record = self._record_for(state, phase.name)
            if record.status == PhaseStatus.COMPLETED:
                logger.debug("Skipping completed phase %s", phase.name.value)
                continue
            outcome = await self._run_one(phase, state, store, work_dir, ticket_path, record)
            exit_code = self._exit_code_from(outcome)
            if exit_code is not None:
                state.run_status = self._run_status_from(outcome)
                state.exit_code = exit_code
                await store.save(state)
                return exit_code
        state.run_status = RunStatus.COMPLETED
        state.exit_code = EXIT_OK
        await store.save(state)
        return EXIT_OK

    async def _run_one(
        self,
        phase: Phase,
        state: State,
        store: StateStore,
        work_dir: Path,
        ticket_path: str,
        record: PhaseRecord,
    ) -> PhaseOutcome:
        ctx = PhaseContext(state=state, work_dir=work_dir, ticket_path=ticket_path)
        record.status = PhaseStatus.RUNNING
        record.started_at = datetime.now(UTC)
        state.current_phase = phase.name
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
