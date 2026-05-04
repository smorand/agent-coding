"""Search tools: ripgrep, find by name, ast-grep.

Each tool wraps a binary via the injectable `SubprocessRunner`. Defaults to
`AsyncSubprocessRunner`; tests inject a fake. Binary names are constants and
can be overridden in the constructor (lets tests target a known location).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_RIPGREP = "rg"
DEFAULT_AST_GREP = "ast-grep"
DEFAULT_FIND = "find"

GREP_MAX_MATCHES = 500


class GrepTool:
    """Run ripgrep across the workspace."""

    name = "grep"
    description = (
        "Search the workspace with ripgrep for `pattern`. Optional `glob` (e.g., '*.py') "
        "and `path` (relative). Returns matching lines, capped at 500."
    )

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_RIPGREP,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def call(
        self,
        pattern: str,
        glob: str | None = None,
        path: str = ".",
    ) -> ToolResult:
        """Search `pattern` under `path`, optionally limited to files matching `glob`."""
        argv: list[str] = [
            self._binary,
            "--no-heading",
            "--with-filename",
            "--line-number",
            "--max-count",
            str(GREP_MAX_MATCHES),
        ]
        if glob is not None:
            argv += ["--glob", glob]
        argv += ["--", pattern, path]
        outcome = await self._runner.run(argv, cwd=self._workspace)
        if outcome.returncode == 0:
            return ToolResult(ok=True, output=outcome.stdout, metadata={"matches": _count_lines(outcome.stdout)})
        if outcome.returncode == 1:
            return ToolResult(ok=True, output="", metadata={"matches": 0})
        return ToolResult(
            ok=False,
            output=outcome.stdout,
            error=outcome.stderr or f"ripgrep exited {outcome.returncode}",
        )


class FindFilesTool:
    """Find files by name pattern (uses Python pathlib glob, not the find binary)."""

    name = "find"
    description = "Find files matching a glob pattern (e.g., '**/*.py') under `path` (relative)."

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, name_pattern: str, path: str = ".") -> ToolResult:
        """Glob `name_pattern` under `path` and return matched paths, one per line."""
        return await asyncio.to_thread(self._find_sync, name_pattern, path)

    def _find_sync(self, name_pattern: str, path: str) -> ToolResult:
        base = (self._workspace / path).resolve()
        try:
            base.relative_to(self._workspace.resolve())
        except ValueError:
            return ToolResult(ok=False, error=f"Path {path!r} escapes workspace")
        if not base.exists():
            return ToolResult(ok=False, error=f"Path not found: {base}")
        matches = sorted(str(p.relative_to(self._workspace)) for p in Path(base).rglob(name_pattern))
        return ToolResult(ok=True, output="\n".join(matches), metadata={"count": len(matches)})


class AstGrepTool:
    """Run ast-grep with a structural pattern in a given language."""

    name = "ast_grep"
    description = (
        "Structural search via ast-grep. `pattern` uses ast-grep meta-variable syntax; "
        "`lang` is a language id (e.g., 'python'); `path` is relative to the workspace."
    )

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_AST_GREP,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def call(self, pattern: str, lang: str, path: str = ".") -> ToolResult:
        """Search the AST of `lang` files under `path` for `pattern`."""
        argv = [self._binary, "run", "--pattern", pattern, "--lang", lang, path]
        outcome = await self._runner.run(argv, cwd=self._workspace)
        if outcome.returncode == 0:
            return ToolResult(ok=True, output=outcome.stdout)
        return ToolResult(
            ok=False,
            output=outcome.stdout,
            error=outcome.stderr or f"ast-grep exited {outcome.returncode}",
        )


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
