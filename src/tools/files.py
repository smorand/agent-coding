"""File operations exposed as agent tools.

All file ops are async (wrap pathlib via `asyncio.to_thread`). Paths are
resolved against an injected workspace root; this lets the future anti-cheat
wrapper enforce zone restrictions on top of these tools without changing
their signatures.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from tools.base import ToolResult

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_READ_MAX_BYTES = 1_000_000  # 1 MiB-ish hard cap on read_file


def _resolve(workspace: Path, relative: str) -> Path:
    """Resolve a relative path against the workspace, refusing escapes."""
    candidate = (workspace / relative).resolve()
    workspace_resolved = workspace.resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError as exc:
        msg = f"Path {relative!r} escapes workspace {workspace_resolved}"
        raise ValueError(msg) from exc
    return candidate


class ReadFileTool:
    """Read a UTF-8 text file, optionally between line ranges."""

    name = "read_file"
    description = "Read a UTF-8 text file, optionally between two line numbers (1-indexed, inclusive)."

    __slots__ = ("_max_bytes", "_workspace")

    def __init__(self, workspace: Path, *, max_bytes: int = DEFAULT_READ_MAX_BYTES) -> None:
        self._workspace = workspace
        self._max_bytes = max_bytes

    async def call(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> ToolResult:
        """Read the file at `path` (relative to workspace)."""
        try:
            target = _resolve(self._workspace, path)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._read_sync, target, start, end)

    def _read_sync(self, target: Path, start: int | None, end: int | None) -> ToolResult:
        if not target.exists():
            return ToolResult(ok=False, error=f"File not found: {target}")
        if not target.is_file():
            return ToolResult(ok=False, error=f"Not a regular file: {target}")
        size = target.stat().st_size
        if size > self._max_bytes and start is None and end is None:
            return ToolResult(
                ok=False,
                error=f"File {target} is {size} bytes (> {self._max_bytes}); pass start/end to slice",
            )
        text = target.read_text(encoding="utf-8")
        if start is None and end is None:
            return ToolResult(ok=True, output=text, metadata={"bytes": size})
        lines = text.splitlines(keepends=True)
        s = (start - 1) if start is not None else 0
        e = end if end is not None else len(lines)
        s = max(s, 0)
        e = min(e, len(lines))
        return ToolResult(ok=True, output="".join(lines[s:e]), metadata={"start": s + 1, "end": e})


class WriteFileTool:
    """Create or overwrite a file with the given content."""

    name = "write_file"
    description = "Create or overwrite a file with content. Creates parent directories as needed."

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, path: str, content: str) -> ToolResult:
        """Write `content` to `path` (relative to workspace) atomically."""
        try:
            target = _resolve(self._workspace, path)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._write_sync, target, content)

    def _write_sync(self, target: Path, content: str) -> ToolResult:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target)
        logger.debug("Wrote %s bytes to %s", len(content), target)
        return ToolResult(ok=True, output=str(target), metadata={"bytes": len(content)})


class EditFileTool:
    """Replace exactly one occurrence of `old_string` with `new_string`."""

    name = "edit_file"
    description = (
        "Replace exactly one occurrence of `old_string` with `new_string` in the file. "
        "Fails if `old_string` does not match exactly once."
    )

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, path: str, old_string: str, new_string: str) -> ToolResult:
        """Single string-replace on the file."""
        try:
            target = _resolve(self._workspace, path)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._edit_sync, target, old_string, new_string)

    def _edit_sync(self, target: Path, old_string: str, new_string: str) -> ToolResult:
        if not target.exists():
            return ToolResult(ok=False, error=f"File not found: {target}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            return ToolResult(ok=False, error="old_string not found in file")
        if count > 1:
            return ToolResult(
                ok=False,
                error=f"old_string matches {count} times; expand context to disambiguate",
            )
        new_text = text.replace(old_string, new_string, 1)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(target)
        return ToolResult(ok=True, output=str(target), metadata={"replacements": 1})


class ListDirTool:
    """List the entries of a directory (one entry per line)."""

    name = "list_dir"
    description = "List entries of a directory at `path`, one per line; directories suffixed with /."

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, path: str = ".") -> ToolResult:
        """List immediate entries of `path`."""
        try:
            target = _resolve(self._workspace, path)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._list_sync, target)

    def _list_sync(self, target: Path) -> ToolResult:
        if not target.exists():
            return ToolResult(ok=False, error=f"Path not found: {target}")
        if not target.is_dir():
            return ToolResult(ok=False, error=f"Not a directory: {target}")
        entries = sorted(target.iterdir(), key=lambda p: p.name)
        lines = [f"{p.name}/" if p.is_dir() else p.name for p in entries]
        return ToolResult(ok=True, output="\n".join(lines), metadata={"count": len(lines)})


class DeleteFileTool:
    """Remove a file (NOT a directory)."""

    name = "delete_file"
    description = "Remove a file (not a directory). Fails if the path does not exist or is a directory."

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, path: str) -> ToolResult:
        """Delete the file at `path`."""
        try:
            target = _resolve(self._workspace, path)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._delete_sync, target)

    def _delete_sync(self, target: Path) -> ToolResult:
        if not target.exists():
            return ToolResult(ok=False, error=f"File not found: {target}")
        if target.is_dir():
            return ToolResult(ok=False, error=f"Refusing to delete a directory: {target}")
        target.unlink()
        return ToolResult(ok=True, output=str(target))


class MoveFileTool:
    """Move or rename a file."""

    name = "move_file"
    description = "Move or rename a file from `src` to `dst` (both relative to workspace)."

    __slots__ = ("_workspace",)

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    async def call(self, src: str, dst: str) -> ToolResult:
        """Move `src` to `dst` within the workspace."""
        try:
            source = _resolve(self._workspace, src)
            destination = _resolve(self._workspace, dst)
        except ValueError as exc:
            return ToolResult(ok=False, error=str(exc))
        return await asyncio.to_thread(self._move_sync, source, destination)

    def _move_sync(self, source: Path, destination: Path) -> ToolResult:
        if not source.exists():
            return ToolResult(ok=False, error=f"Source not found: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return ToolResult(ok=True, output=str(destination))
