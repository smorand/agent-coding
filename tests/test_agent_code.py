"""CLI smoke tests for the agent-code Typer app."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agent_code import _ticket_id_from, app

runner = CliRunner()


def test_run_missing_ticket_exits_with_system_error(tmp_path: Path) -> None:
    """Invoking `run` on a missing file exits with code 3 (system error)."""
    missing = tmp_path / "nope.md"
    result = runner.invoke(app, ["run", str(missing)])
    assert result.exit_code == 3
    assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower()


def test_run_completes_with_skeleton_pipeline(tmp_path: Path) -> None:
    """A valid ticket runs the skeleton pipeline end-to-end and exits 0."""
    ticket = tmp_path / "demo.md"
    ticket.write_text("# demo\n", encoding="utf-8")
    result = runner.invoke(app, ["run", str(ticket), "--workspace", str(tmp_path)])
    assert result.exit_code == 0


def test_ticket_id_from_strips_extension_and_normalizes() -> None:
    """The slug strips the extension and lowercases."""
    assert _ticket_id_from(Path("/x/y/Add-Subtract.md")) == "add-subtract"


def test_ticket_id_from_replaces_special_chars() -> None:
    """Non-slug characters become hyphens; leading and trailing hyphens trimmed."""
    assert _ticket_id_from(Path("Foo Bar! v2.md")) == "foo-bar-v2"


def test_ticket_id_from_empty_stem_falls_back() -> None:
    """A stem that becomes empty after slug normalization falls back to 'ticket'."""
    assert _ticket_id_from(Path("/path/to/!.md")) == "ticket"
