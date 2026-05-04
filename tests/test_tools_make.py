"""Tests for the make tool using a fake subprocess runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.base import SubprocessOutcome
from tools.make_runner import MakeTool

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass
class FakeRunner:
    """Capture argv; return a configured outcome."""

    outcome: SubprocessOutcome
    calls: list[list[str]]

    @classmethod
    def returning(cls, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> FakeRunner:
        return cls(outcome=SubprocessOutcome(returncode, stdout, stderr), calls=[])

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: object = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        self.calls.append(list(argv))
        return self.outcome


async def test_make_target_runs_and_succeeds(tmp_path: Path) -> None:
    """make exit 0 produces ok=True with combined stdout+stderr."""
    runner = FakeRunner.returning(returncode=0, stdout="All checks passed!\n")
    result = await MakeTool(tmp_path, runner=runner).call(target="check")
    assert result.ok is True
    assert "All checks passed!" in result.output
    assert result.metadata["target"] == "check"
    assert runner.calls[0] == ["make", "check"]


async def test_make_target_failure_returns_returncode(tmp_path: Path) -> None:
    """A non-zero exit yields ok=False with returncode in metadata."""
    runner = FakeRunner.returning(returncode=2, stdout="error\n", stderr="boom\n")
    result = await MakeTool(tmp_path, runner=runner).call(target="check")
    assert result.ok is False
    assert "error" in result.output and "boom" in result.output
    assert result.metadata == {"target": "check", "returncode": 2}


async def test_make_empty_target_rejected(tmp_path: Path) -> None:
    """Empty target name is rejected before invocation."""
    runner = FakeRunner.returning(returncode=0)
    result = await MakeTool(tmp_path, runner=runner).call(target="   ")
    assert result.ok is False
    assert "Empty" in result.error
    assert runner.calls == []
