"""`apply_patch` tool: apply a unified diff to one or more files.

Wraps `git apply` (with `--unsafe-paths` deliberately NOT set), which is
the canonical way to apply a unified diff in a workspace where `git`
already knows about the files. Multi-location refactors that touch
several hunks across the same file are the primary use case; for single
small edits, `edit_file` remains preferred.

The wrapper validates two things up-front:
- The argument `diff` is non-empty.
- Every `+++ <path>` and `--- <path>` header in the diff resolves to a
  path under the workspace (no `..`, no absolute leak). Any header
  pointing outside the workspace is rejected with `ValueError` before
  spawning git.

Anti-cheat: paths matching `is_test_locked_path` (i.e. `tests/test_*.py`
under most layouts) are rejected the same way the file-write tools are.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from tools.anti_cheat import is_test_locked_path
from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_GIT = "git"

# Match the path field of a unified-diff header. We strip the standard
# `a/` and `b/` prefixes and discard the `/dev/null` markers used for
# additions / deletions.
_HEADER_PATTERN = re.compile(r"^(?:---|\+\+\+)\s+(?P<path>\S+)", re.MULTILINE)
_PREFIX_RE = re.compile(r"^[ab]/")


class ApplyPatchTool:
    """Apply a unified diff to the workspace via `git apply`."""

    name = "apply_patch"
    description = (
        "Apply a unified diff (multi-file, multi-hunk). The diff must use "
        "git-style `--- a/path` / `+++ b/path` headers. Paths must stay "
        "inside the workspace and must not target locked test files."
    )

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

    async def call(self, **kwargs: Any) -> ToolResult:
        """Apply `diff` (str) to the workspace; reject locked / escaping paths."""
        diff = kwargs.get("diff")
        if not isinstance(diff, str) or not diff.strip():
            return ToolResult(ok=False, error="argument 'diff': required non-empty string")
        try:
            paths = extract_diff_paths(diff)
        except ValueError as exc:
            return ToolResult(ok=False, error=f"invalid diff: {exc}")
        for path in paths:
            if is_test_locked_path(path):
                return ToolResult(
                    ok=False,
                    error=f"diff targets locked test path: {path}",
                )
        check_only = bool(kwargs.get("check_only", False))
        argv = [self._binary, "apply"]
        if check_only:
            argv.append("--check")
        argv.append("-")  # read patch from stdin
        outcome = await self._runner.run(argv, cwd=self._workspace, input_text=diff)
        if outcome.returncode == 0:
            return ToolResult(
                ok=True,
                output=outcome.stdout or f"applied {len(paths)} path(s)",
                metadata={"paths": list(paths)},
            )
        return ToolResult(
            ok=False,
            output=outcome.stdout,
            error=outcome.stderr or f"git apply exited {outcome.returncode}",
        )


def extract_diff_paths(diff: str) -> tuple[str, ...]:
    """Extract file paths referenced in a unified diff.

    Strips `a/` / `b/` prefixes, drops `/dev/null` (used for adds/deletes),
    rejects absolute paths and `..` traversal. Raises `ValueError` if no
    headers are found at all.
    """
    paths: list[str] = []
    seen: set[str] = set()
    for match in _HEADER_PATTERN.finditer(diff):
        raw = match.group("path").strip()
        if raw == "/dev/null":
            continue
        cleaned = _PREFIX_RE.sub("", raw, count=1)
        if cleaned.startswith("/"):
            msg = f"absolute path in header: {raw}"
            raise ValueError(msg)
        if any(part == ".." for part in cleaned.split("/")):
            msg = f"path traversal in header: {raw}"
            raise ValueError(msg)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            paths.append(cleaned)
    if not paths:
        msg = "no '--- '/'+++ ' headers found"
        raise ValueError(msg)
    return tuple(paths)
