"""Tests for the anti-cheat wrapper around the tool registry."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from state import PhaseName
from tools.anti_cheat import (
    BLOCK_REASON,
    AntiCheatGuard,
    AuditTrail,
    BlockedCall,
    is_test_locked_path,
)
from tools.base import ToolResult
from tools.registry import ToolRegistry


class _RecordingTool:
    """Minimal Tool that records calls and returns a configurable result."""

    def __init__(self, name: str, result: ToolResult | None = None) -> None:
        self.name = name
        self.description = f"recording {name}"
        self._result = result or ToolResult(ok=True, output=f"{name} ran")
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return self._result


# ---------------------------------------------------------------------------
# is_test_locked_path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_foo.py",
        "tests/test_bar.py",
        "tests/sub/test_x.py",
        "tests/functional/test_api.py",
    ],
)
def test_is_test_locked_path_locks_test_files(path: str) -> None:
    """Any tests/test_*.py at any depth (outside testdata) is locked."""
    assert is_test_locked_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "tests/sub/conftest.py",
        "tests/testdata/sample.json",
        "tests/testdata/test_input.py",
        "tests/__init__.py",
        "src/test_x.py",  # outside tests/
        "src/x.py",
        "scripts/test_runner.py",
        "",
        "tests",
    ],
)
def test_is_test_locked_path_allows_other_paths(path: str) -> None:
    """conftest.py, testdata/**, and non-tests paths are NOT locked."""
    assert is_test_locked_path(path) is False


def test_is_test_locked_path_handles_windows_separators() -> None:
    """Backslash separators are normalized to POSIX before checking."""
    assert is_test_locked_path("tests\\test_foo.py") is True


# ---------------------------------------------------------------------------
# AntiCheatGuard, non-implementation phases
# ---------------------------------------------------------------------------


async def test_guard_passes_through_when_no_phase_set() -> None:
    """Without a phase, the guard never blocks (initial state)."""
    tool = _RecordingTool("write_file")
    registry = ToolRegistry([tool])
    guard = AntiCheatGuard(registry)
    result = await guard.call("write_file", path="tests/test_foo.py", content="x")
    assert result.ok is True
    assert tool.calls == [{"path": "tests/test_foo.py", "content": "x"}]
    assert guard.blocked_calls == ()


async def test_guard_passes_through_in_planning_phase() -> None:
    """Phases other than IMPLEMENTATION never block, even on locked paths."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.PLANNING)
    result = await guard.call("write_file", path="tests/test_foo.py", content="x")
    assert result.ok is True
    assert tool.calls == [{"path": "tests/test_foo.py", "content": "x"}]


async def test_guard_passes_through_in_e2e_writing_phase() -> None:
    """The E2E writing phase legitimately writes tests/test_*.py and is NOT blocked."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.E2E_WRITING)
    result = await guard.call("write_file", path="tests/test_subtract.py", content="x")
    assert result.ok is True
    assert tool.calls and tool.calls[0]["path"] == "tests/test_subtract.py"


# ---------------------------------------------------------------------------
# AntiCheatGuard, IMPLEMENTATION phase blocks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "kwargs"),
    [
        ("write_file", {"path": "tests/test_foo.py", "content": "x"}),
        ("edit_file", {"path": "tests/test_foo.py", "old_string": "a", "new_string": "b"}),
        ("delete_file", {"path": "tests/test_foo.py"}),
    ],
)
async def test_guard_blocks_writes_to_locked_paths_during_implementation(
    tool_name: str, kwargs: dict[str, Any]
) -> None:
    """write/edit/delete on tests/test_*.py during IMPLEMENTATION returns ok=False, blocked."""
    tool = _RecordingTool(tool_name)
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call(tool_name, **kwargs)

    assert result.ok is False
    assert result.error == BLOCK_REASON
    assert result.metadata == {
        "blocked": True,
        "tool": tool_name,
        "paths": [kwargs.get("path")],
    }
    # The wrapped tool was never called.
    assert tool.calls == []
    assert len(guard.blocked_calls) == 1
    assert guard.blocked_calls[0].tool_name == tool_name


async def test_guard_blocks_move_when_dst_is_locked() -> None:
    """A move with a locked dst is blocked (preventing renames into tests/test_*.py)."""
    tool = _RecordingTool("move_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("move_file", src="tests/conftest.py", dst="tests/test_foo.py")

    assert result.ok is False
    assert "tests/test_foo.py" in result.metadata["paths"]


async def test_guard_blocks_move_when_src_is_locked() -> None:
    """A move OUT of a locked test file is also blocked (renaming away is still a write)."""
    tool = _RecordingTool("move_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("move_file", src="tests/test_foo.py", dst="src/foo.py")

    assert result.ok is False
    assert "tests/test_foo.py" in result.metadata["paths"]


async def test_guard_allows_writes_to_conftest_during_implementation() -> None:
    """conftest.py is allowed during IMPLEMENTATION (shared fixtures)."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("write_file", path="tests/conftest.py", content="x")

    assert result.ok is True
    assert tool.calls == [{"path": "tests/conftest.py", "content": "x"}]


async def test_guard_allows_writes_under_testdata_during_implementation() -> None:
    """tests/testdata/** is allowed during IMPLEMENTATION (golden fixtures)."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("write_file", path="tests/testdata/sample.json", content="{}")

    assert result.ok is True


async def test_guard_allows_writes_to_src_during_implementation() -> None:
    """Writes outside tests/ are unaffected."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("write_file", path="src/foo.py", content="def x(): ...\n")

    assert result.ok is True


async def test_guard_does_not_block_read_only_tools() -> None:
    """Read tools are never blocked, regardless of path or phase."""
    tool = _RecordingTool("read_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    result = await guard.call("read_file", path="tests/test_foo.py")

    assert result.ok is True
    assert tool.calls == [{"path": "tests/test_foo.py"}]


async def test_guard_records_multiple_blocks_in_order() -> None:
    """Multiple block events accumulate in `blocked_calls` in chronological order."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)

    await guard.call("write_file", path="tests/test_a.py", content="x")
    await guard.call("write_file", path="tests/test_b.py", content="y")
    await guard.call("write_file", path="tests/test_c.py", content="z")

    blocked = guard.blocked_calls
    assert len(blocked) == 3
    paths = [b.paths[0] for b in blocked]
    assert paths == ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


async def test_guard_set_phase_to_none_disables_enforcement() -> None:
    """Setting phase to None disables blocking (orchestrator phase boundary)."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)
    guard.set_phase(None)

    result = await guard.call("write_file", path="tests/test_foo.py", content="x")
    assert result.ok is True


def test_guard_names_delegates_to_registry() -> None:
    """`names` returns the underlying registry's tool names."""
    registry = ToolRegistry([_RecordingTool("read_file"), _RecordingTool("write_file")])
    guard = AntiCheatGuard(registry)
    assert guard.names == ("read_file", "write_file")


# ---------------------------------------------------------------------------
# AuditTrail
# ---------------------------------------------------------------------------


async def test_audit_trail_extend_and_jsonl_round_trip() -> None:
    """AuditTrail.extend appends; to_jsonl emits one valid JSON object per line."""
    tool = _RecordingTool("write_file")
    guard = AntiCheatGuard(ToolRegistry([tool]))
    guard.set_phase(PhaseName.IMPLEMENTATION)
    await guard.call("write_file", path="tests/test_a.py", content="x")
    await guard.call("write_file", path="tests/test_b.py", content="y")

    trail = AuditTrail()
    trail.extend(guard.blocked_calls)

    jsonl = trail.to_jsonl()
    lines = jsonl.splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert {p["paths"][0] for p in parsed} == {"tests/test_a.py", "tests/test_b.py"}
    for entry in parsed:
        assert entry["phase"] == PhaseName.IMPLEMENTATION.value
        assert entry["tool"] == "write_file"
        assert "timestamp" in entry
        assert "reason" in entry


def test_audit_trail_empty_renders_empty_string() -> None:
    """An empty AuditTrail renders to an empty string."""
    trail = AuditTrail()
    assert trail.to_jsonl() == ""


def test_blocked_call_is_immutable() -> None:
    """BlockedCall is a frozen dataclass."""
    call = BlockedCall(
        timestamp=datetime(2026, 5, 4, tzinfo=UTC),
        phase="implementation",
        tool_name="write_file",
        paths=("tests/test_x.py",),
    )
    try:
        call.tool_name = "edit_file"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    msg = "BlockedCall should be immutable"
    raise AssertionError(msg)
