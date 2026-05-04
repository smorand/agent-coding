"""Tests for the ClassificationPhase wrapper."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases.base import OutcomeKind, PhaseContext
from phases.classification import CLASSIFICATION_REPORT_FILENAME, ClassificationPhase
from state import PhaseName, PhaseRecord, State

if TYPE_CHECKING:
    from pathlib import Path


def _state() -> State:
    now = datetime(2026, 5, 4, tzinfo=UTC)
    return State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.CLASSIFICATION,
        phases=[PhaseRecord(name=PhaseName.CLASSIFICATION)],
    )


def _ctx(workspace: Path, ticket_path: Path) -> PhaseContext:
    work_dir = workspace / ".agent_work" / "demo"
    return PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket_path))


async def test_classification_continues_on_python_workspace(tmp_path: Path) -> None:
    """A workspace with pyproject.toml passes classification."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, ticket)

    outcome = await ClassificationPhase().run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    payload = json.loads((ctx.work_dir / CLASSIFICATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["project_type"] == "python"
    assert payload["is_supported"] is True
    assert "pyproject.toml" in payload["markers"]


async def test_classification_continues_on_empty_workspace(tmp_path: Path) -> None:
    """An empty workspace (only ticket + .git) is treated as a bootstrap candidate."""
    (tmp_path / ".git").mkdir()
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, ticket)

    outcome = await ClassificationPhase().run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE


async def test_classification_halts_on_node_workspace(tmp_path: Path) -> None:
    """A workspace with package.json is rejected with a clear message."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, ticket)

    outcome = await ClassificationPhase().run(ctx)

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "node" in outcome.message
    assert "package.json" in outcome.message


async def test_classification_halts_on_unknown_workspace(tmp_path: Path) -> None:
    """A workspace with no markers and unknown content is rejected."""
    (tmp_path / "main.c").write_text("int main(){}", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, ticket)

    outcome = await ClassificationPhase().run(ctx)

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "Could not determine" in outcome.message


async def test_classification_persists_report_even_on_failure(tmp_path: Path) -> None:
    """The classification.json is written on every run, success or failure."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    ctx = _ctx(tmp_path, ticket)

    await ClassificationPhase().run(ctx)

    payload = json.loads((ctx.work_dir / CLASSIFICATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["project_type"] == "rust"
    assert payload["is_supported"] is False
    assert "Cargo.toml" in payload["markers"]


async def test_classification_creates_work_dir_when_missing(tmp_path: Path) -> None:
    """The phase creates the work dir on demand to write its report."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    work_dir = tmp_path / ".agent_work" / "demo" / "deep"
    ctx = PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket))
    assert not work_dir.exists()

    await ClassificationPhase().run(ctx)

    assert (work_dir / CLASSIFICATION_REPORT_FILENAME).exists()
