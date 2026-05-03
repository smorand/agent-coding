"""Smoke test for the agent-code entry point.

Replace or extend with real E2E tests as the project gets actual features.
"""

from typer.testing import CliRunner

from agent_code import app

runner = CliRunner()


def test_hello_default() -> None:
    """The default `hello` command greets the world."""
    result = runner.invoke(app, ["hello"])
    assert result.exit_code == 0
    assert "Hello, World!" in result.stdout


def test_hello_custom_name() -> None:
    """The `hello` command greets the provided name."""
    result = runner.invoke(app, ["hello", "Alice"])
    assert result.exit_code == 0
    assert "Hello, Alice!" in result.stdout
