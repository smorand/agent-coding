"""Tests for the file operation tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.files import (
    DeleteFileTool,
    EditFileTool,
    ListDirTool,
    MoveFileTool,
    ReadFileTool,
    WriteFileTool,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_read_file_returns_full_text(tmp_path: Path) -> None:
    """A small file is returned in full with byte count metadata."""
    target = tmp_path / "hello.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")
    result = await ReadFileTool(tmp_path).call(path="hello.txt")
    assert result.ok is True
    assert result.output == "hello\nworld\n"
    assert result.metadata["bytes"] == 12


async def test_read_file_supports_line_range(tmp_path: Path) -> None:
    """`start` and `end` slice by 1-indexed inclusive line numbers."""
    target = tmp_path / "lines.txt"
    target.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    result = await ReadFileTool(tmp_path).call(path="lines.txt", start=2, end=4)
    assert result.ok is True
    assert result.output == "b\nc\nd\n"
    assert result.metadata == {"start": 2, "end": 4}


async def test_read_file_missing_file_returns_not_ok(tmp_path: Path) -> None:
    """A missing file is a soft failure with explanatory error."""
    result = await ReadFileTool(tmp_path).call(path="absent.txt")
    assert result.ok is False
    assert "not found" in result.error.lower()


async def test_read_file_too_large_without_slice_is_rejected(tmp_path: Path) -> None:
    """A file larger than max_bytes without start/end is rejected."""
    target = tmp_path / "big.txt"
    target.write_text("x" * 100, encoding="utf-8")
    result = await ReadFileTool(tmp_path, max_bytes=10).call(path="big.txt")
    assert result.ok is False
    assert "100 bytes" in result.error


async def test_read_file_rejects_path_escape(tmp_path: Path) -> None:
    """A path that resolves outside the workspace is rejected."""
    result = await ReadFileTool(tmp_path).call(path="../escape.txt")
    assert result.ok is False
    assert "escape" in result.error.lower()


async def test_write_file_creates_file_and_parents(tmp_path: Path) -> None:
    """Writing creates intermediate directories."""
    result = await WriteFileTool(tmp_path).call(path="sub/dir/new.txt", content="payload")
    assert result.ok is True
    target = tmp_path / "sub" / "dir" / "new.txt"
    assert target.read_text(encoding="utf-8") == "payload"
    assert result.metadata["bytes"] == 7


async def test_write_file_overwrites_existing(tmp_path: Path) -> None:
    """Writing to an existing file replaces its content atomically."""
    target = tmp_path / "f.txt"
    target.write_text("old", encoding="utf-8")
    result = await WriteFileTool(tmp_path).call(path="f.txt", content="new")
    assert result.ok is True
    assert target.read_text(encoding="utf-8") == "new"
    assert not (tmp_path / "f.txt.tmp").exists()


async def test_edit_file_replaces_unique_match(tmp_path: Path) -> None:
    """A unique `old_string` is replaced exactly once."""
    target = tmp_path / "f.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")
    result = await EditFileTool(tmp_path).call(path="f.py", old_string="return 1", new_string="return 2")
    assert result.ok is True
    assert "return 2" in target.read_text(encoding="utf-8")


async def test_edit_file_rejects_zero_matches(tmp_path: Path) -> None:
    """An old_string that does not appear is a soft failure."""
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    result = await EditFileTool(tmp_path).call(path="f.txt", old_string="bye", new_string="hi")
    assert result.ok is False
    assert "not found" in result.error.lower()


async def test_edit_file_rejects_multiple_matches(tmp_path: Path) -> None:
    """An old_string that appears multiple times is rejected (ambiguous)."""
    target = tmp_path / "f.txt"
    target.write_text("a\na\na\n", encoding="utf-8")
    result = await EditFileTool(tmp_path).call(path="f.txt", old_string="a", new_string="b")
    assert result.ok is False
    assert "matches 3 times" in result.error


async def test_list_dir_returns_sorted_entries(tmp_path: Path) -> None:
    """Directory listing is alphabetical and tags directories with /."""
    (tmp_path / "z.txt").write_text("z", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    result = await ListDirTool(tmp_path).call(path=".")
    assert result.ok is True
    assert result.output.splitlines() == ["a.txt", "subdir/", "z.txt"]
    assert result.metadata["count"] == 3


async def test_list_dir_missing_path(tmp_path: Path) -> None:
    """Listing a missing path is a soft failure."""
    result = await ListDirTool(tmp_path).call(path="absent")
    assert result.ok is False
    assert "not found" in result.error.lower()


async def test_delete_file_removes_existing(tmp_path: Path) -> None:
    """Delete removes a regular file."""
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    result = await DeleteFileTool(tmp_path).call(path="f.txt")
    assert result.ok is True
    assert not target.exists()


async def test_delete_file_refuses_directory(tmp_path: Path) -> None:
    """Delete refuses to act on a directory."""
    (tmp_path / "d").mkdir()
    result = await DeleteFileTool(tmp_path).call(path="d")
    assert result.ok is False
    assert "directory" in result.error.lower()


async def test_move_file_renames_within_workspace(tmp_path: Path) -> None:
    """Move renames a file, creating destination parent directories."""
    (tmp_path / "src.txt").write_text("payload", encoding="utf-8")
    result = await MoveFileTool(tmp_path).call(src="src.txt", dst="dst/dir/dst.txt")
    assert result.ok is True
    assert (tmp_path / "dst/dir/dst.txt").read_text(encoding="utf-8") == "payload"
    assert not (tmp_path / "src.txt").exists()


async def test_move_file_missing_source(tmp_path: Path) -> None:
    """Move from a non-existent source is a soft failure."""
    result = await MoveFileTool(tmp_path).call(src="absent.txt", dst="dst.txt")
    assert result.ok is False
    assert "source" in result.error.lower()
