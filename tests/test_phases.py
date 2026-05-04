"""Tests for the phase package: pipeline composition and stub behavior."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases import PIPELINE
from phases.base import PhaseContext, PhaseOutcome
from state import PhaseName, PhaseRecord, State

if TYPE_CHECKING:
    from pathlib import Path


def test_pipeline_has_seven_phases_in_canonical_order() -> None:
    """The PIPELINE tuple contains the seven phases in spec order."""
    expected = (
        PhaseName.CLASSIFICATION,
        PhaseName.DOR,
        PhaseName.COMPREHENSION,
        PhaseName.PLANNING,
        PhaseName.E2E_WRITING,
        PhaseName.IMPLEMENTATION,
        PhaseName.REVIEW,
    )
    assert tuple(phase.name for phase in PIPELINE) == expected


def test_each_phase_name_is_unique() -> None:
    """No two phases share a name."""
    names = [phase.name for phase in PIPELINE]
    assert len(set(names)) == len(names)


async def test_each_phase_skeleton_returns_continue_outcome(tmp_path: Path) -> None:
    """Every skeleton phase returns the default CONTINUE outcome."""
    state = _bare_state()
    ctx = PhaseContext(state=state, work_dir=tmp_path, ticket_path="dummy.md")

    for phase in PIPELINE:
        await phase.prepare(ctx)
        outcome = await phase.run(ctx)
        await phase.checkpoint(ctx)
        assert isinstance(outcome, PhaseOutcome)
        assert outcome.kind.value == "continue"


def _bare_state() -> State:
    now = datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)
    return State(
        ticket_id="t",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.CLASSIFICATION,
        phases=[PhaseRecord(name=name) for name in PhaseName],
    )
