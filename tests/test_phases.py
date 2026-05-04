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
    """Every PIPELINE phase returns CONTINUE on a minimal valid ticket.

    The DoR phase is the first to have real logic (FR-004): it requires a
    ticket file that satisfies the canonical template. The other phases are
    still logging stubs that ignore ticket_path.
    """
    state = _bare_state()
    ticket = tmp_path / "ticket.md"
    ticket.write_text(
        (
            "---\n"
            "id: pipe-smoke\n"
            "title: Smoke ticket for the pipeline test\n"
            "---\n\n"
            "## Description\n\n"
            "A minimal ticket used to exercise the PIPELINE without invoking real "
            "phase logic beyond DoR validation.\n\n"
            "## Acceptance Criteria\n\n"
            "- AC-1: every PIPELINE phase returns the CONTINUE outcome.\n"
        ),
        encoding="utf-8",
    )
    ctx = PhaseContext(state=state, work_dir=tmp_path, ticket_path=str(ticket))

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
