"""Tests for the apply_patch tool."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tools.apply_patch import ApplyPatchTool, extract_diff_paths
from tools.base import SubprocessOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class FakeRunner:
    """SubprocessRunner test double that records argv and stdin."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self._rc = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        del cwd, timeout
        self.calls.append(list(argv))
        self.inputs.append(input_text)
        return SubprocessOutcome(returncode=self._rc, stdout=self._stdout, stderr=self._stderr)


_VALID_DIFF = "diff --git a/src/foo.py b/src/foo.py\n--- a/src/foo.py\n+++ b/src/foo.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"

_TEST_LOCKED_DIFF = "--- a/tests/test_locked.py\n+++ b/tests/test_locked.py\n@@ -1 +1 @@\n-x\n+y\n"


def test_extract_diff_paths_strips_a_b_prefixes() -> None:
    """Headers with `a/`/`b/` prefixes yield the bare relative paths."""
    paths = extract_diff_paths(_VALID_DIFF)
    assert paths == ("src/foo.py",)


def test_extract_diff_paths_skips_dev_null() -> None:
    """A `--- /dev/null` header is ignored (file creation)."""
    diff = "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    assert extract_diff_paths(diff) == ("src/new.py",)


def test_extract_diff_paths_rejects_traversal() -> None:
    """A `..` segment in a header path raises ValueError."""
    diff = "--- a/../etc/passwd\n+++ b/../etc/passwd\n"
    with pytest.raises(ValueError, match="path traversal"):
        extract_diff_paths(diff)


def test_extract_diff_paths_rejects_absolute() -> None:
    """An absolute path in a header raises ValueError."""
    diff = "--- /etc/passwd\n+++ /etc/passwd\n"
    with pytest.raises(ValueError, match="absolute path"):
        extract_diff_paths(diff)


def test_extract_diff_paths_requires_at_least_one_header() -> None:
    """A diff body without `---` / `+++` headers is rejected."""
    with pytest.raises(ValueError, match="no '--- '"):
        extract_diff_paths("not a diff")


async def test_apply_patch_success(tmp_path: Path) -> None:
    """A valid diff invokes `git apply` via stdin and returns ok=True."""
    runner = FakeRunner(returncode=0, stdout="")
    tool = ApplyPatchTool(tmp_path, runner=runner)

    result = await tool.call(diff=_VALID_DIFF)

    assert result.ok
    assert runner.calls[0][:2] == ["git", "apply"]
    assert runner.calls[0][-1] == "-"
    assert runner.inputs[0] == _VALID_DIFF
    assert "src/foo.py" in result.metadata["paths"]


async def test_apply_patch_check_only_flag(tmp_path: Path) -> None:
    """`check_only=True` adds the --check flag without committing the change."""
    runner = FakeRunner(returncode=0)
    tool = ApplyPatchTool(tmp_path, runner=runner)

    await tool.call(diff=_VALID_DIFF, check_only=True)

    assert "--check" in runner.calls[0]


async def test_apply_patch_rejects_locked_test_path(tmp_path: Path) -> None:
    """A diff targeting `tests/test_*.py` is rejected before invoking git."""
    runner = FakeRunner()
    tool = ApplyPatchTool(tmp_path, runner=runner)

    result = await tool.call(diff=_TEST_LOCKED_DIFF)

    assert not result.ok
    assert "locked" in result.error
    assert runner.calls == []


async def test_apply_patch_rejects_empty_diff(tmp_path: Path) -> None:
    """An empty diff is rejected before invoking git."""
    runner = FakeRunner()
    tool = ApplyPatchTool(tmp_path, runner=runner)

    result = await tool.call(diff="")

    assert not result.ok
    assert "diff" in result.error
    assert runner.calls == []


async def test_apply_patch_surfaces_git_failure(tmp_path: Path) -> None:
    """A non-zero exit from `git apply` propagates as ok=False with stderr."""
    runner = FakeRunner(returncode=1, stderr="patch does not apply")
    tool = ApplyPatchTool(tmp_path, runner=runner)

    result = await tool.call(diff=_VALID_DIFF)

    assert not result.ok
    assert "does not apply" in result.error
