"""Tests for the git tools using a fake subprocess runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tools.base import SubprocessOutcome
from tools.git_ops import (
    GitAddTool,
    GitBlameTool,
    GitBranchCreateTool,
    GitCheckoutTool,
    GitCommitTool,
    GitDiffCachedTool,
    GitDiffTool,
    GitLogTool,
    GitResetTool,
    GitStatusTool,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


@dataclass
class FakeRunner:
    """Capture argv for assertion; return a configured outcome."""

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


async def test_git_status_invokes_short(tmp_path: Path) -> None:
    """`git_status` runs `git status --short`."""
    runner = FakeRunner.returning(returncode=0, stdout=" M file.txt\n")
    result = await GitStatusTool(tmp_path, runner=runner).call()
    assert result.ok is True
    assert " M file.txt" in result.output
    assert runner.calls[0] == ["git", "status", "--short"]


async def test_git_diff_without_path(tmp_path: Path) -> None:
    """`git_diff()` runs `git diff` (no path arg)."""
    runner = FakeRunner.returning(returncode=0, stdout="diff --git ...\n")
    await GitDiffTool(tmp_path, runner=runner).call()
    assert runner.calls[0] == ["git", "diff"]


async def test_git_diff_with_path(tmp_path: Path) -> None:
    """`git_diff(path=X)` adds the path to the argv."""
    runner = FakeRunner.returning(returncode=0, stdout="")
    await GitDiffTool(tmp_path, runner=runner).call(path="src/foo.py")
    assert runner.calls[0] == ["git", "diff", "src/foo.py"]


async def test_git_diff_cached(tmp_path: Path) -> None:
    """`git_diff_cached` runs `git diff --cached`."""
    runner = FakeRunner.returning(returncode=0)
    await GitDiffCachedTool(tmp_path, runner=runner).call()
    assert runner.calls[0] == ["git", "diff", "--cached"]


async def test_git_add_passes_paths(tmp_path: Path) -> None:
    """`git_add` runs `git add -- <paths...>`."""
    runner = FakeRunner.returning(returncode=0)
    await GitAddTool(tmp_path, runner=runner).call(paths=["a.py", "b.py"])
    assert runner.calls[0] == ["git", "add", "--", "a.py", "b.py"]


async def test_git_add_empty_is_noop(tmp_path: Path) -> None:
    """An empty paths list is a no-op (no subprocess call)."""
    runner = FakeRunner.returning(returncode=0)
    result = await GitAddTool(tmp_path, runner=runner).call(paths=[])
    assert result.ok is True
    assert runner.calls == []


async def test_git_commit_with_message(tmp_path: Path) -> None:
    """`git_commit(message=...)` runs `git commit -m <msg>`."""
    runner = FakeRunner.returning(returncode=0)
    await GitCommitTool(tmp_path, runner=runner).call(message="a commit")
    assert runner.calls[0] == ["git", "commit", "-m", "a commit"]


async def test_git_commit_empty_message_rejected(tmp_path: Path) -> None:
    """Empty message is rejected before any subprocess call."""
    runner = FakeRunner.returning(returncode=0)
    result = await GitCommitTool(tmp_path, runner=runner).call(message="   ")
    assert result.ok is False
    assert runner.calls == []


async def test_git_log_default_limit(tmp_path: Path) -> None:
    """`git_log` defaults to a 20-entry oneline log."""
    runner = FakeRunner.returning(returncode=0, stdout="abc Initial\n")
    await GitLogTool(tmp_path, runner=runner).call()
    assert runner.calls[0] == ["git", "log", "--oneline", "-n20"]


async def test_git_log_with_limit_and_path(tmp_path: Path) -> None:
    """Limit and path are forwarded to git log."""
    runner = FakeRunner.returning(returncode=0)
    await GitLogTool(tmp_path, runner=runner).call(path="src/", limit=5)
    assert runner.calls[0] == ["git", "log", "--oneline", "-n5", "--", "src/"]


async def test_git_log_zero_limit_rejected(tmp_path: Path) -> None:
    """A non-positive limit is rejected before any subprocess call."""
    runner = FakeRunner.returning(returncode=0)
    result = await GitLogTool(tmp_path, runner=runner).call(limit=0)
    assert result.ok is False
    assert runner.calls == []


async def test_git_blame_single_line(tmp_path: Path) -> None:
    """`git_blame` blames a single line via -L<n>,<n>."""
    runner = FakeRunner.returning(returncode=0)
    await GitBlameTool(tmp_path, runner=runner).call(path="src/x.py", line=42)
    assert runner.calls[0] == ["git", "blame", "-L", "42,42", "--", "src/x.py"]


async def test_git_blame_zero_line_rejected(tmp_path: Path) -> None:
    """A non-positive line is rejected."""
    runner = FakeRunner.returning(returncode=0)
    result = await GitBlameTool(tmp_path, runner=runner).call(path="x.py", line=0)
    assert result.ok is False


async def test_git_branch_create(tmp_path: Path) -> None:
    """`git_branch_create` runs `git checkout -b <name>`."""
    runner = FakeRunner.returning(returncode=0)
    await GitBranchCreateTool(tmp_path, runner=runner).call(name="feat/x")
    assert runner.calls[0] == ["git", "checkout", "-b", "feat/x"]


async def test_git_checkout(tmp_path: Path) -> None:
    """`git_checkout` runs `git checkout <ref>`."""
    runner = FakeRunner.returning(returncode=0)
    await GitCheckoutTool(tmp_path, runner=runner).call(ref="main")
    assert runner.calls[0] == ["git", "checkout", "main"]


async def test_git_reset_default_mixed(tmp_path: Path) -> None:
    """`git_reset` defaults to --mixed."""
    runner = FakeRunner.returning(returncode=0)
    await GitResetTool(tmp_path, runner=runner).call(target="HEAD")
    assert runner.calls[0] == ["git", "reset", "--mixed", "HEAD"]


async def test_git_reset_hard(tmp_path: Path) -> None:
    """`git_reset(mode='hard')` runs `git reset --hard <target>`."""
    runner = FakeRunner.returning(returncode=0)
    await GitResetTool(tmp_path, runner=runner).call(target="HEAD~1", mode="hard")
    assert runner.calls[0] == ["git", "reset", "--hard", "HEAD~1"]


async def test_git_reset_invalid_mode_rejected(tmp_path: Path) -> None:
    """Unknown mode is rejected."""
    runner = FakeRunner.returning(returncode=0)
    result = await GitResetTool(tmp_path, runner=runner).call(target="HEAD", mode="bogus")
    assert result.ok is False
    assert "mode" in result.error
    assert runner.calls == []


async def test_git_failure_surfaces_stderr(tmp_path: Path) -> None:
    """A non-zero git exit returns ok=False with stderr in error."""
    runner = FakeRunner.returning(returncode=128, stderr="not a git repo\n")
    result = await GitStatusTool(tmp_path, runner=runner).call()
    assert result.ok is False
    assert "not a git repo" in result.error
