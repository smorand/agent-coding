"""Tests for the DoR phase wrapper around the validator."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases.base import OutcomeKind, PhaseContext
from phases.dor import DOR_REPORT_FILENAME, DorPhase
from state import PhaseName, PhaseRecord, State

if TYPE_CHECKING:
    from pathlib import Path

READY_TICKET = """\
---
id: add-subtract
title: Add a subtract function to calc
created: 2026-05-04
author: smorand
---

# Add a subtract function to calc

## Description

The calc module currently exposes only `add`. We need a symmetric `subtract`
function for our internal arithmetic helpers.

## Acceptance Criteria

- AC-1: calc.subtract(5, 3) returns the integer 2.
- AC-2: calc.subtract(0, 0) returns the integer 0.
"""

NOT_READY_TICKET = """\
---
id: incomplete
title: Incomplete ticket
---

# Incomplete

## Description

short.
"""


def _state(ticket_id: str = "demo") -> State:
    now = datetime(2026, 5, 4, tzinfo=UTC)
    return State(
        ticket_id=ticket_id,
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.DOR,
        phases=[PhaseRecord(name=PhaseName.DOR)],
    )


async def test_dor_phase_continues_on_ready_ticket(tmp_path: Path) -> None:
    """A READY ticket yields a CONTINUE outcome and writes the report."""
    ticket = tmp_path / "ticket.md"
    ticket.write_text(READY_TICKET, encoding="utf-8")
    work_dir = tmp_path / ".agent_work" / "demo"
    ctx = PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket))

    outcome = await DorPhase().run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    # Report file was persisted.
    report_path = work_dir / DOR_REPORT_FILENAME
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "READY"
    assert payload["issues"] == []
    # Ticket was not modified.
    assert ticket.read_text(encoding="utf-8") == READY_TICKET


async def test_dor_phase_halts_and_appends_comment_on_not_ready(tmp_path: Path) -> None:
    """A NOT_READY ticket yields HALT_DOR_FAILED and the canonical comment is appended."""
    ticket = tmp_path / "ticket.md"
    ticket.write_text(NOT_READY_TICKET, encoding="utf-8")
    work_dir = tmp_path / ".agent_work" / "incomplete"
    ctx = PhaseContext(state=_state("incomplete"), work_dir=work_dir, ticket_path=str(ticket))

    outcome = await DorPhase().run(ctx)

    assert outcome.kind == OutcomeKind.HALT_DOR_FAILED
    # Comment was appended at the end of the file.
    text = ticket.read_text(encoding="utf-8")
    assert text.startswith(NOT_READY_TICKET)
    assert "<!-- agent-code DoR report" in text
    assert "**Status**: NOT_READY" in text
    assert "<!-- end agent-code DoR report -->" in text
    # Report was also persisted as JSON.
    payload = json.loads((work_dir / DOR_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["status"] == "NOT_READY"
    assert payload["issues"]


async def test_dor_phase_creates_work_dir_when_missing(tmp_path: Path) -> None:
    """The phase creates .agent_work/<ticket_id>/ on demand."""
    ticket = tmp_path / "ticket.md"
    ticket.write_text(READY_TICKET, encoding="utf-8")
    work_dir = tmp_path / ".agent_work" / "demo" / "deep" / "nested"
    assert not work_dir.exists()
    ctx = PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket))

    await DorPhase().run(ctx)

    assert (work_dir / DOR_REPORT_FILENAME).exists()


async def test_dor_phase_records_agent_version_in_comment(tmp_path: Path) -> None:
    """The agent version passed to the constructor appears in the appended comment."""
    ticket = tmp_path / "ticket.md"
    ticket.write_text(NOT_READY_TICKET, encoding="utf-8")
    work_dir = tmp_path / ".agent_work" / "demo"
    ctx = PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket))

    await DorPhase(agent_version="9.9.9-test").run(ctx)

    assert "**Agent version**: 9.9.9-test" in ticket.read_text(encoding="utf-8")
