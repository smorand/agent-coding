"""Phase interface contract.

Every phase in the pipeline implements `Phase`. The orchestrator calls
`prepare`, `run`, and `checkpoint` in sequence. The phase decides what to do
based on the `PhaseContext` (the run state and side resources) and returns a
`PhaseOutcome` indicating whether to proceed, halt, or record a soft failure.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from state import PhaseName, State


class OutcomeKind(StrEnum):
    """How the orchestrator should react to a phase outcome."""

    CONTINUE = "continue"
    HALT_OK = "halt_ok"
    HALT_DOR_FAILED = "halt_dor_failed"
    HALT_EXHAUSTED = "halt_exhausted"
    HALT_ERROR = "halt_error"


@dataclass(frozen=True)
class PhaseContext:
    """Inputs handed to a phase by the orchestrator.

    `state` is the live run state (mutated by the orchestrator after the phase
    returns). `work_dir` is `.agent_work/<ticket_id>/`. `ticket_path` is the
    path or URL of the user story being processed.
    """

    state: State
    work_dir: Path
    ticket_path: str


@dataclass(frozen=True)
class PhaseOutcome:
    """Result returned by a phase to the orchestrator."""

    kind: OutcomeKind = OutcomeKind.CONTINUE
    message: str = ""
    notes: dict[str, str] = field(default_factory=dict)


class Phase(ABC):
    """Abstract base for every pipeline phase.

    Subclasses set `name` to one of `PhaseName` and implement `run`. `prepare`
    and `checkpoint` are optional hooks with sensible defaults; subclasses
    override them when they need to acquire resources or write artifacts.
    """

    name: PhaseName

    async def prepare(self, ctx: PhaseContext) -> None:  # noqa: B027
        """Acquire resources before `run`. Default: no-op (override when needed)."""

    @abstractmethod
    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Execute the phase. Returns the outcome that drives the orchestrator."""

    async def checkpoint(self, ctx: PhaseContext) -> None:  # noqa: B027
        """Persist phase-local artifacts after `run`. Default: no-op (override when needed)."""
