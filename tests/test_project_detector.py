"""Tests for the pure project-type detector (FR-003)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from phases.project_detector import (
    SUPPORTED_TYPES,
    DetectionResult,
    ProjectType,
    detect_project_type,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_python_pyproject_marker_yields_python(tmp_path: Path) -> None:
    """A workspace with pyproject.toml is detected as PYTHON, supported."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.PYTHON
    assert "pyproject.toml" in result.markers
    assert result.is_supported is True


def test_node_marker_yields_node_unsupported(tmp_path: Path) -> None:
    """A workspace with package.json is detected as NODE, unsupported."""
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.NODE
    assert result.is_supported is False


def test_rust_marker_yields_rust_unsupported(tmp_path: Path) -> None:
    """Cargo.toml means Rust, unsupported in MVP."""
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.RUST
    assert result.is_supported is False


def test_go_marker_yields_go_unsupported(tmp_path: Path) -> None:
    """go.mod means Go, unsupported."""
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.GO
    assert result.is_supported is False


@pytest.mark.parametrize("marker", ["pom.xml", "build.gradle", "build.gradle.kts"])
def test_java_markers_yield_java_unsupported(tmp_path: Path, marker: str) -> None:
    """Maven and Gradle markers all classify as Java, unsupported."""
    (tmp_path / marker).write_text("", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.JAVA
    assert result.is_supported is False


def test_python_takes_priority_over_other_markers(tmp_path: Path) -> None:
    """A polyglot workspace with both pyproject.toml and package.json is PYTHON."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.PYTHON
    assert result.is_supported is True


def test_empty_workspace_yields_empty_supported(tmp_path: Path) -> None:
    """A directory with no source markers (only .git) is EMPTY, supported (bootstrap target)."""
    (tmp_path / ".git").mkdir()
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.EMPTY
    assert result.is_supported is True


def test_empty_workspace_with_just_ticket_yields_empty(tmp_path: Path) -> None:
    """A directory containing only the ticket file and ignored entries is EMPTY."""
    (tmp_path / ".git").mkdir()
    ticket = tmp_path / "ticket.md"
    ticket.write_text("# x\n", encoding="utf-8")
    result = detect_project_type(tmp_path, ticket_path=ticket)
    assert result.project_type == ProjectType.EMPTY


def test_unknown_workspace_with_random_files_is_unknown(tmp_path: Path) -> None:
    """A workspace with non-marker source files (no pyproject.toml etc.) is UNKNOWN."""
    (tmp_path / "main.c").write_text("int main(){}", encoding="utf-8")
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.UNKNOWN
    assert result.is_supported is False


def test_missing_workspace_is_unknown(tmp_path: Path) -> None:
    """A non-existent workspace path returns UNKNOWN, unsupported."""
    missing = tmp_path / "no-such-dir"
    result = detect_project_type(missing)
    assert result.project_type == ProjectType.UNKNOWN
    assert result.is_supported is False


def test_workspace_path_is_a_file_is_unknown(tmp_path: Path) -> None:
    """A path that exists but is not a directory is UNKNOWN, unsupported."""
    target = tmp_path / "not-a-dir.txt"
    target.write_text("x", encoding="utf-8")
    result = detect_project_type(target)
    assert result.project_type == ProjectType.UNKNOWN
    assert result.is_supported is False


def test_ignored_top_level_entries_do_not_break_empty_detection(tmp_path: Path) -> None:
    """`.gitignore`, `README.md`, `LICENSE`, `specs/`, `vars/`, `tickets/`, `.agent_work/` are tolerated."""
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("", encoding="utf-8")
    (tmp_path / "specs").mkdir()
    (tmp_path / "vars").mkdir()
    (tmp_path / "tickets").mkdir()
    (tmp_path / ".agent_work").mkdir()
    result = detect_project_type(tmp_path)
    assert result.project_type == ProjectType.EMPTY


def test_supported_types_set_is_python_and_empty() -> None:
    """The MVP supported set is exactly Python and Empty."""
    assert frozenset({ProjectType.PYTHON, ProjectType.EMPTY}) == SUPPORTED_TYPES


def test_detection_result_is_immutable() -> None:
    """DetectionResult is a frozen dataclass."""
    result = DetectionResult(ProjectType.PYTHON, ("pyproject.toml",), is_supported=True)
    try:
        result.is_supported = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    msg = "DetectionResult should be immutable"
    raise AssertionError(msg)
