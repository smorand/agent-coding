"""Git operations exposed as agent tools.

Wrappers around the `git` binary via the injectable `SubprocessRunner`.
Each wrapper builds a small, typed argv (no shell, no string interpolation
into argv).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_GIT = "git"
DEFAULT_LOG_LIMIT = 20


class _BaseGitTool:
    """Common state for git tools (binary path + runner + workspace).

    Not a `Tool` itself; concrete subclasses satisfy the `Tool` Protocol
    structurally by declaring `name`, `description`, and `call`.
    """

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_GIT,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def _git(self, *args: str) -> ToolResult:
        argv: list[str] = [self._binary, *args]
        outcome = await self._runner.run(argv, cwd=self._workspace)
        if outcome.returncode == 0:
            return ToolResult(ok=True, output=outcome.stdout)
        return ToolResult(
            ok=False,
            output=outcome.stdout,
            error=outcome.stderr or f"git exited {outcome.returncode}",
        )


class GitStatusTool(_BaseGitTool):
    """Run `git status --short`."""

    name = "git_status"
    description = "Show working tree status in short form."

    async def call(self) -> ToolResult:
        """Return short-form working tree status."""
        return await self._git("status", "--short")


class GitDiffTool(_BaseGitTool):
    """Run `git diff` (unstaged) optionally on a path."""

    name = "git_diff"
    description = "Show unstaged changes; optional `path` filters output to that path."

    async def call(self, path: str | None = None) -> ToolResult:
        """Return the unstaged diff."""
        return await self._git("diff", path) if path else await self._git("diff")


class GitDiffCachedTool(_BaseGitTool):
    """Run `git diff --cached` (staged)."""

    name = "git_diff_cached"
    description = "Show staged changes (the next commit's content)."

    async def call(self) -> ToolResult:
        """Return the staged diff."""
        return await self._git("diff", "--cached")


class GitAddTool(_BaseGitTool):
    """Run `git add <paths...>`."""

    name = "git_add"
    description = "Stage one or more paths for the next commit."

    async def call(self, paths: Sequence[str]) -> ToolResult:
        """Stage `paths`. Empty list is treated as no-op."""
        if not paths:
            return ToolResult(ok=True, output="")
        return await self._git("add", "--", *paths)


class GitCommitTool(_BaseGitTool):
    """Run `git commit -m <message>`."""

    name = "git_commit"
    description = "Create a commit with the given message from staged changes."

    async def call(self, message: str) -> ToolResult:
        """Create a commit. Fails if no staged changes."""
        if not message.strip():
            return ToolResult(ok=False, error="Empty commit message rejected")
        return await self._git("commit", "-m", message)


class GitLogTool(_BaseGitTool):
    """Run `git log --oneline -n <limit> [-- <path>]`."""

    name = "git_log"
    description = "Show oneline commit history, capped at `limit` (default 20); optional `path` scope."

    async def call(
        self,
        path: str | None = None,
        limit: int = DEFAULT_LOG_LIMIT,
    ) -> ToolResult:
        """Return short commit history."""
        if limit <= 0:
            return ToolResult(ok=False, error="limit must be > 0")
        args = ["log", "--oneline", f"-n{limit}"]
        if path:
            args += ["--", path]
        return await self._git(*args)


class GitBlameTool(_BaseGitTool):
    """Run `git blame -L <line>,<line> <path>`."""

    name = "git_blame"
    description = "Show the commit that last touched `line` (1-indexed) of `path`."

    async def call(self, path: str, line: int) -> ToolResult:
        """Blame a single line of a file."""
        if line <= 0:
            return ToolResult(ok=False, error="line must be > 0")
        return await self._git("blame", "-L", f"{line},{line}", "--", path)


class GitBranchCreateTool(_BaseGitTool):
    """Run `git checkout -b <name>`."""

    name = "git_branch_create"
    description = "Create and switch to a new branch."

    async def call(self, name: str) -> ToolResult:
        """Create branch `name` and switch to it."""
        if not name.strip():
            return ToolResult(ok=False, error="Empty branch name rejected")
        return await self._git("checkout", "-b", name)


class GitCheckoutTool(_BaseGitTool):
    """Run `git checkout <ref>`."""

    name = "git_checkout"
    description = "Switch to the given branch or commit."

    async def call(self, ref: str) -> ToolResult:
        """Checkout `ref`."""
        if not ref.strip():
            return ToolResult(ok=False, error="Empty ref rejected")
        return await self._git("checkout", ref)


class GitResetTool(_BaseGitTool):
    """Run `git reset --<mode> <target>`."""

    name = "git_reset"
    description = "Reset HEAD to `target` with mode in {soft, mixed, hard}."

    __slots__ = ()

    _ALLOWED_MODES = ("soft", "mixed", "hard")

    async def call(self, target: str, mode: str = "mixed") -> ToolResult:
        """Reset to `target`."""
        if mode not in self._ALLOWED_MODES:
            return ToolResult(ok=False, error=f"mode must be one of {self._ALLOWED_MODES}, got {mode!r}")
        return await self._git("reset", f"--{mode}", target)
