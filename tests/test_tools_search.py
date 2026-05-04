"""Tests for grep, find, ast-grep tools using a fake subprocess runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.base import SubprocessOutcome
from tools.search import AstGrepTool, FindFilesTool, GrepTool

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass
class FakeRunner:
    """Records calls and returns a configured outcome."""

    outcome: SubprocessOutcome
    calls: list[Sequence[str]]

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


async def test_grep_success_returns_matches(tmp_path: Path) -> None:
    """rg exit 0 produces ok=True with the captured stdout."""
    runner = FakeRunner.returning(returncode=0, stdout="src/x.py:1:def foo()\n")
    tool = GrepTool(tmp_path, runner=runner)
    result = await tool.call(pattern="foo", glob="*.py")
    assert result.ok is True
    assert "src/x.py:1:def foo()" in result.output
    assert result.metadata["matches"] == 1
    argv = runner.calls[0]
    assert "--glob" in argv
    assert "*.py" in argv
    assert "foo" in argv


async def test_grep_no_matches_returns_ok_with_zero(tmp_path: Path) -> None:
    """rg exit 1 (no matches) is a successful 'no matches' result."""
    runner = FakeRunner.returning(returncode=1)
    tool = GrepTool(tmp_path, runner=runner)
    result = await tool.call(pattern="nope")
    assert result.ok is True
    assert result.output == ""
    assert result.metadata["matches"] == 0


async def test_grep_other_exit_is_failure(tmp_path: Path) -> None:
    """Any rg exit > 1 is reported as failure with stderr surfaced."""
    runner = FakeRunner.returning(returncode=2, stderr="bad regex")
    tool = GrepTool(tmp_path, runner=runner)
    result = await tool.call(pattern="???")
    assert result.ok is False
    assert "bad regex" in result.error


async def test_find_returns_globbed_paths(tmp_path: Path) -> None:
    """`find` walks pathlib.rglob and returns relative paths sorted."""
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.txt").write_text("", encoding="utf-8")
    tool = FindFilesTool(tmp_path)
    result = await tool.call(name_pattern="*.py")
    assert result.ok is True
    assert result.output.splitlines() == ["a.py", "sub/b.py"]
    assert result.metadata["count"] == 2


async def test_find_missing_path(tmp_path: Path) -> None:
    """A non-existent search path is a soft failure."""
    tool = FindFilesTool(tmp_path)
    result = await tool.call(name_pattern="*.py", path="absent")
    assert result.ok is False
    assert "not found" in result.error.lower()


async def test_find_rejects_path_escape(tmp_path: Path) -> None:
    """A `path` that resolves outside the workspace is rejected."""
    tool = FindFilesTool(tmp_path)
    result = await tool.call(name_pattern="*.py", path="..")
    assert result.ok is False
    assert "escape" in result.error.lower()


async def test_ast_grep_success(tmp_path: Path) -> None:
    """ast-grep exit 0 surfaces stdout."""
    runner = FakeRunner.returning(returncode=0, stdout="match: src/x.py:1\n")
    tool = AstGrepTool(tmp_path, runner=runner)
    result = await tool.call(pattern="def $X():", lang="python")
    assert result.ok is True
    assert "match" in result.output


async def test_ast_grep_failure(tmp_path: Path) -> None:
    """ast-grep non-zero exit is reported as failure with stderr."""
    runner = FakeRunner.returning(returncode=2, stderr="invalid pattern")
    tool = AstGrepTool(tmp_path, runner=runner)
    result = await tool.call(pattern="???", lang="python")
    assert result.ok is False
    assert "invalid pattern" in result.error
