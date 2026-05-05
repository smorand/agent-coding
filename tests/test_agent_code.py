"""CLI smoke tests for the agent-code Typer app."""

from __future__ import annotations

import asyncio
from pathlib import Path

from typer.testing import CliRunner

from agent_code import _build_pipeline_components, _ticket_id_from, app

runner = CliRunner()


def test_run_missing_ticket_exits_with_system_error(tmp_path: Path) -> None:
    """Invoking `run` on a missing file exits with code 3 (system error)."""
    missing = tmp_path / "nope.md"
    result = runner.invoke(app, ["run", str(missing)])
    assert result.exit_code == 3
    assert "not found" in result.stderr.lower() or "not found" in result.stdout.lower()


def test_run_completes_with_skeleton_pipeline(tmp_path: Path) -> None:
    """A valid ticket runs the skeleton pipeline end-to-end and exits 0."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
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
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
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


def _minimal_valid_yaml(template_path: str = "/opt/agent-code/templates/python") -> str:
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
        + f"template_path: {template_path}\n"
        + "mcp:\n  context7:\n    url: http://c:1\n  duckduckgo:\n    url: http://d:1\n"
    )


def test_run_with_config_bootstraps_empty_workspace_end_to_end(tmp_path: Path) -> None:
    """An empty workspace + config.yaml with template_path bootstraps and exits 0."""
    # Build a minimal template OUTSIDE the workspace.
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "tests").mkdir()
    (template / ".template_version").write_text("0.1.0\n", encoding="utf-8")
    (template / "pyproject.toml").write_text("[project]\nname = '__PROJECT_NAME__'\n", encoding="utf-8")
    (template / "src" / "__PROJECT_ENTRY__.py").write_text("# entry for __PROJECT_NAME__\n", encoding="utf-8")
    # Workspace: empty except for .git and the ticket.
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    ticket = workspace / "ticket.md"
    ticket.write_text(
        (
            "---\n"
            "id: my-feature\n"
            "title: Bootstrap and run\n"
            "author: Tester\n"
            "---\n\n"
            "## Description\n\n"
            "A ticket that triggers bootstrap from the configured template.\n\n"
            "## Acceptance Criteria\n\n"
            "- AC-1: the workspace gets populated with the template files.\n"
        ),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_valid_yaml(template_path=str(template)), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "run",
            str(ticket),
            "--workspace",
            str(workspace),
            "--config",
            str(config_path),
        ],
    )

    assert result.exit_code == 0, result.stdout + result.stderr
    # Bootstrap actually happened.
    assert (workspace / "pyproject.toml").exists()
    assert (workspace / "src" / "my_feature.py").exists()
    # Classification report records the bootstrap.
    classification = workspace / ".agent_work" / "ticket" / "classification.json"
    assert classification.exists()
    assert "bootstrap" in classification.read_text(encoding="utf-8")


def test_run_with_invalid_config_falls_back_to_default_pipeline(tmp_path: Path) -> None:
    """If the config is invalid, the run continues with default phases (no template_path)."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    ticket = tmp_path / "ticket.md"
    ticket.write_text(
        (
            "---\n"
            "id: demo\n"
            "title: Demo with invalid config\n"
            "---\n\n"
            "## Description\n\n"
            "A demo ticket used to verify that an invalid config file is tolerated.\n\n"
            "## Acceptance Criteria\n\n"
            "- AC-1: the run still proceeds.\n"
        ),
        encoding="utf-8",
    )
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("phases: [unclosed", encoding="utf-8")

    result = runner.invoke(
        app,
        ["run", str(ticket), "--workspace", str(tmp_path), "--config", str(bad_config)],
    )

    # Run completes because the workspace already has pyproject.toml.
    assert result.exit_code == 0


def test_pipeline_components_without_config_has_no_tools() -> None:
    """Without a config file, the registry and factory are both None."""
    components = _build_pipeline_components(Path("/this/path/should/not/exist.yaml"))

    assert components.tools is None
    assert components.mcp_factory is None
    assert len(components.phases) == 7


def test_pipeline_components_with_valid_config_registers_mcp_tools(tmp_path: Path) -> None:
    """A valid config produces a registry containing the three MCP tools."""
    template = tmp_path / "template"
    template.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_minimal_valid_yaml(template_path=str(template)), encoding="utf-8")

    components = _build_pipeline_components(config_path)
    try:
        assert components.tools is not None
        assert components.mcp_factory is not None
        assert components.tools.names == ("query_docs", "resolve_library_id", "search_web")
    finally:
        if components.mcp_factory is not None:
            asyncio.run(components.mcp_factory.aclose())
