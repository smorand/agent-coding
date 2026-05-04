"""Project type detection (FR-003).

Pure file-system inspection of a workspace to identify the project type.
Deterministic by design; the spec reserves a small-model disambiguation step
for edge cases, but in MVP only Python is supported and the rules below
suffice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class ProjectType(StrEnum):
    """Detected project type."""

    PYTHON = "python"
    NODE = "node"
    RUST = "rust"
    GO = "go"
    JAVA = "java"
    EMPTY = "empty"  # workspace contains no source markers (bootstrap candidate)
    UNKNOWN = "unknown"


SUPPORTED_TYPES: frozenset[ProjectType] = frozenset({ProjectType.PYTHON, ProjectType.EMPTY})

PYTHON_MARKERS: tuple[str, ...] = ("pyproject.toml",)
NODE_MARKERS: tuple[str, ...] = ("package.json",)
RUST_MARKERS: tuple[str, ...] = ("Cargo.toml",)
GO_MARKERS: tuple[str, ...] = ("go.mod",)
JAVA_MARKERS: tuple[str, ...] = ("pom.xml", "build.gradle", "build.gradle.kts")

_IGNORED_TOP_LEVEL: frozenset[str] = frozenset(
    {
        ".git",
        ".github",
        ".gitignore",
        ".agent_work",
        "specs",
        "vars",
        "tickets",
        "README.md",
        "LICENSE",
    }
)


@dataclass(frozen=True)
class DetectionResult:
    """Outcome of `detect_project_type`."""

    project_type: ProjectType
    markers: tuple[str, ...]
    is_supported: bool


def detect_project_type(workspace: Path, *, ticket_path: Path | None = None) -> DetectionResult:
    """Inspect `workspace` and return the detected project type.

    `ticket_path` is excluded from the empty-workspace check (a directory
    that contains only the ticket and a `.git/` is treated as EMPTY, a valid
    bootstrap target). Markers are checked in priority order: Python > Node
    > Rust > Go > Java > empty > unknown.
    """
    if not workspace.exists() or not workspace.is_dir():
        return DetectionResult(ProjectType.UNKNOWN, (), is_supported=False)

    found = _find_markers(workspace)
    if found[ProjectType.PYTHON]:
        return DetectionResult(ProjectType.PYTHON, found[ProjectType.PYTHON], is_supported=True)
    for non_python in (ProjectType.NODE, ProjectType.RUST, ProjectType.GO, ProjectType.JAVA):
        if found[non_python]:
            return DetectionResult(non_python, found[non_python], is_supported=False)

    if _looks_empty(workspace, ticket_path=ticket_path):
        return DetectionResult(ProjectType.EMPTY, (), is_supported=True)
    return DetectionResult(ProjectType.UNKNOWN, (), is_supported=False)


def _find_markers(workspace: Path) -> dict[ProjectType, tuple[str, ...]]:
    return {
        ProjectType.PYTHON: tuple(name for name in PYTHON_MARKERS if (workspace / name).exists()),
        ProjectType.NODE: tuple(name for name in NODE_MARKERS if (workspace / name).exists()),
        ProjectType.RUST: tuple(name for name in RUST_MARKERS if (workspace / name).exists()),
        ProjectType.GO: tuple(name for name in GO_MARKERS if (workspace / name).exists()),
        ProjectType.JAVA: tuple(name for name in JAVA_MARKERS if (workspace / name).exists()),
    }


def _looks_empty(workspace: Path, *, ticket_path: Path | None) -> bool:
    """True when `workspace` has no project source beyond ignored entries.

    Used to identify bootstrap targets. `tickets/` and the ticket file itself
    are tolerated; so are `.git`, README, LICENSE, etc.
    """
    ticket_name = ticket_path.name if ticket_path is not None else None
    for entry in workspace.iterdir():
        if entry.name in _IGNORED_TOP_LEVEL:
            continue
        if ticket_name is not None and entry.name == ticket_name:
            continue
        return False
    return True
