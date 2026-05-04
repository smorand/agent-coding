"""Tests for the default async subprocess runner."""

from __future__ import annotations

import sys

import pytest

from tools.base import ToolError
from tools.runner import AsyncSubprocessRunner


async def test_run_captures_stdout_and_returncode() -> None:
    """A simple command captures stdout and exit code."""
    runner = AsyncSubprocessRunner()
    result = await runner.run([sys.executable, "-c", "print('hello')"])
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert result.stderr == ""


async def test_run_captures_stderr_and_nonzero_returncode() -> None:
    """A command writing to stderr and exiting non-zero is captured."""
    runner = AsyncSubprocessRunner()
    result = await runner.run([sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(7)"])
    assert result.returncode == 7
    assert "boom" in result.stderr


async def test_run_unknown_binary_raises_tool_error() -> None:
    """A binary that does not exist raises ToolError."""
    runner = AsyncSubprocessRunner()
    with pytest.raises(ToolError, match="Cannot spawn"):
        await runner.run(["definitely-not-a-binary-xyz"])


async def test_run_timeout_raises_tool_error() -> None:
    """A process exceeding the timeout is killed and ToolError is raised."""
    runner = AsyncSubprocessRunner()
    with pytest.raises(ToolError, match="exceeded timeout"):
        await runner.run(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=0.2,
        )


async def test_run_passes_input_text() -> None:
    """`input_text` is forwarded to stdin."""
    runner = AsyncSubprocessRunner()
    result = await runner.run(
        [sys.executable, "-c", "import sys; print(sys.stdin.read())"],
        input_text="payload",
    )
    assert result.returncode == 0
    assert "payload" in result.stdout
