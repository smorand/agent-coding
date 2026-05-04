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
    ticket.write_text(
        (
            "---\n"
            "id: demo\n"
            "title: Demo ticket for the skeleton pipeline\n"
            "---\n\n"
            "## Description\n\n"
            "A demo ticket used to verify the skeleton pipeline runs end to end "
            "without invoking real model endpoints.\n\n"
            "## Acceptance Criteria\n\n"
            "- AC-1: the pipeline reaches the review phase and exits cleanly.\n"
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", str(ticket), "--workspace", str(tmp_path)])
    assert result.exit_code == 0


def test_run_with_not_ready_ticket_exits_with_dor_failed(tmp_path: Path) -> None:
    """An incomplete ticket triggers HALT_DOR_FAILED and exits 1 (not 0 or 3)."""
    ticket = tmp_path / "incomplete.md"
    ticket.write_text(
        "---\nid: incomplete\ntitle: Incomplete ticket\n---\n\n## Description\n\nshort.\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["run", str(ticket), "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    # The DoR phase appended its comment to the ticket file.
    body = ticket.read_text(encoding="utf-8")
    assert "<!-- agent-code DoR report" in body
    assert "**Status**: NOT_READY" in body


def test_ticket_id_from_strips_extension_and_normalizes() -> None:
    """The slug strips the extension and lowercases."""
    assert _ticket_id_from(Path("/x/y/Add-Subtract.md")) == "add-subtract"


def test_ticket_id_from_replaces_special_chars() -> None:
    """Non-slug characters become hyphens; leading and trailing hyphens trimmed."""
    assert _ticket_id_from(Path("Foo Bar! v2.md")) == "foo-bar-v2"


def test_ticket_id_from_empty_stem_falls_back() -> None:
    """A stem that becomes empty after slug normalization falls back to 'ticket'."""
    assert _ticket_id_from(Path("/path/to/!.md")) == "ticket"


def test_check_env_runs_and_returns_an_exit_code() -> None:
    """`check-env` always produces a renderable report and a deterministic exit code."""
    result = runner.invoke(app, ["check-env"])
    assert result.exit_code in {0, 3}
    assert "[OK]" in result.stdout or "[FAIL]" in result.stdout
    assert "python" in result.stdout


def test_config_show_with_missing_file_exits_with_system_error(tmp_path: Path) -> None:
    """`config-show --config <missing>` exits 3 with a clear error."""
    result = runner.invoke(app, ["config-show", "--config", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 3
    assert "No agent-code config" in result.stderr or "No agent-code config" in result.stdout


def test_config_show_renders_loaded_config_as_json(tmp_path: Path) -> None:
    """`config-show --config <valid>` echoes the parsed config as indented JSON."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_valid_yaml(), encoding="utf-8")
    result = runner.invoke(app, ["config-show", "--config", str(config_path)])
    assert result.exit_code == 0
    assert '"phases"' in result.stdout
    assert '"summarizer"' in result.stdout


def _minimal_valid_yaml() -> str:
    body_phases = ""
    for phase in (
        "classification",
        "dor",
        "comprehension",
        "planning",
        "e2e_writing",
        "implementation",
        "review",
        "summarizer",
    ):
        body_phases += f"  {phase}:\n    url: http://localhost:8000/v1\n    model_name: m\n"
    return (
        "phases:\n"
        + body_phases
        + "template_path: /opt/agent-code/templates/python\n"
        + "mcp:\n  context7:\n    url: http://c:1\n  duckduckgo:\n    url: http://d:1\n"
    )
