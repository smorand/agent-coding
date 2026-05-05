"""Tests for the bootstrap module (FR-014)."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bootstrap import (
    AUTHOR_PLACEHOLDER,
    DESCRIPTION_PLACEHOLDER,
    ENTRY_PLACEHOLDER,
    NAME_PLACEHOLDER,
    YEAR_PLACEHOLDER,
    BootstrapError,
    BootstrapInputs,
    extract_inputs_from_ticket,
    materialize_template,
)


def _write(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_bytes(content)
    return path


def _make_template(tmp_path: Path) -> Path:
    """Build a tiny template directory under tmp_path/template/."""
    template = tmp_path / "template"
    _write(template / ".template_version", "0.1.0\n")
    _write(template / "pyproject.toml", '[project]\nname = "__PROJECT_NAME__"\n')
    _write(
        template / "README.md",
        f"# {NAME_PLACEHOLDER}\n\n{DESCRIPTION_PLACEHOLDER}\n\nBy {AUTHOR_PLACEHOLDER} ({YEAR_PLACEHOLDER}).\n",
    )
    _write(
        template / "src" / f"{ENTRY_PLACEHOLDER}.py",
        f"# entry point for {ENTRY_PLACEHOLDER}\n",
    )
    _write(template / "tests" / f"test_{ENTRY_PLACEHOLDER}.py", "")
    _write(template / "src" / "py.typed", "")
    # Add a binary file to verify it is copied byte-for-byte.
    _write(template / "asset.bin", b"\x00\x01\x02")
    return template


# ---------------------------------------------------------------------------
# extract_inputs_from_ticket
# ---------------------------------------------------------------------------


def test_extract_inputs_parses_frontmatter_and_first_paragraph(tmp_path: Path) -> None:
    """The extractor pulls id, title, author, and the first description paragraph."""
    ticket = _write(
        tmp_path / "t.md",
        (
            "---\n"
            "id: add-subtract\n"
            "title: Add subtract\n"
            "author: Alice\n"
            "---\n\n"
            "## Description\n\n"
            "First paragraph that describes the change.\n\n"
            "Second paragraph that should be ignored.\n"
        ),
    )

    inputs = extract_inputs_from_ticket(
        ticket, fallback_email="ignored@example.com", now=datetime(2026, 5, 4, tzinfo=UTC)
    )

    assert inputs.project_name == "add-subtract"
    assert inputs.project_entry == "add_subtract"
    assert inputs.project_description == "First paragraph that describes the change."
    assert inputs.project_author == "Alice"
    assert inputs.project_author_email == "ignored@example.com"
    assert inputs.project_year == 2026
    assert inputs.project_prefix_upper == "ADD_SUBTRACT_"


def test_extract_inputs_falls_back_when_author_missing(tmp_path: Path) -> None:
    """A ticket without `author` uses the fallback."""
    ticket = _write(
        tmp_path / "t.md",
        ("---\nid: x-y-z\ntitle: A\n---\n\n## Description\n\nbody\n"),
    )
    inputs = extract_inputs_from_ticket(ticket, fallback_author="Default Author")
    assert inputs.project_author == "Default Author"


def test_extract_inputs_uses_generic_description_when_section_missing(tmp_path: Path) -> None:
    """A ticket with no Description section uses the generic placeholder text."""
    ticket = _write(tmp_path / "t.md", "---\nid: x-y-z\ntitle: A\n---\n")
    inputs = extract_inputs_from_ticket(ticket)
    assert "Project bootstrapped" in inputs.project_description


def test_extract_inputs_raises_on_missing_file(tmp_path: Path) -> None:
    """A non-existent ticket path raises BootstrapError."""
    with pytest.raises(BootstrapError, match="not found"):
        extract_inputs_from_ticket(tmp_path / "absent.md")


def test_extract_inputs_raises_on_missing_frontmatter(tmp_path: Path) -> None:
    """A ticket without YAML frontmatter raises BootstrapError."""
    ticket = _write(tmp_path / "t.md", "# title\n\nbody\n")
    with pytest.raises(BootstrapError, match="frontmatter"):
        extract_inputs_from_ticket(ticket)


def test_extract_inputs_raises_on_invalid_yaml(tmp_path: Path) -> None:
    """Malformed YAML in frontmatter raises BootstrapError."""
    ticket = _write(tmp_path / "t.md", "---\nid: [unclosed\n---\n")
    with pytest.raises(BootstrapError, match="YAML"):
        extract_inputs_from_ticket(ticket)


def test_extract_inputs_raises_on_missing_id(tmp_path: Path) -> None:
    """A ticket missing `id` raises BootstrapError."""
    ticket = _write(tmp_path / "t.md", "---\ntitle: A\n---\n")
    with pytest.raises(BootstrapError, match="id"):
        extract_inputs_from_ticket(ticket)


# ---------------------------------------------------------------------------
# materialize_template
# ---------------------------------------------------------------------------


def _inputs() -> BootstrapInputs:
    return BootstrapInputs(
        project_name="add-subtract",
        project_entry="add_subtract",
        project_description="A subtract function.",
        project_author="Alice",
        project_author_email="alice@example.com",
        project_year=2026,
    )


def test_materialize_copies_every_file_and_substitutes(tmp_path: Path) -> None:
    """Every text file in the template appears in the workspace with placeholders replaced."""
    template = _make_template(tmp_path)
    workspace = tmp_path / "ws"

    result = materialize_template(workspace, template, _inputs())

    assert result.template_version == "0.1.0"
    pyproject = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "add-subtract"' in pyproject
    readme = (workspace / "README.md").read_text(encoding="utf-8")
    assert "# add-subtract" in readme
    assert "A subtract function." in readme
    assert "By Alice (2026)." in readme


def test_materialize_renames_entry_files(tmp_path: Path) -> None:
    """Files whose name contains __PROJECT_ENTRY__ are renamed at materialization."""
    template = _make_template(tmp_path)
    workspace = tmp_path / "ws"

    materialize_template(workspace, template, _inputs())

    assert (workspace / "src" / "add_subtract.py").exists()
    assert (workspace / "tests" / "test_add_subtract.py").exists()
    # The placeholder filename should not exist under the new path.
    assert not (workspace / "src" / "__PROJECT_ENTRY__.py").exists()


def test_materialize_preserves_binary_files_byte_for_byte(tmp_path: Path) -> None:
    """A binary file (containing 0x00) is copied without substitution."""
    template = _make_template(tmp_path)
    workspace = tmp_path / "ws"

    materialize_template(workspace, template, _inputs())

    assert (workspace / "asset.bin").read_bytes() == b"\x00\x01\x02"


def test_materialize_returns_sorted_relative_paths(tmp_path: Path) -> None:
    """`materialized_files` is a sorted tuple of workspace-relative paths."""
    template = _make_template(tmp_path)
    workspace = tmp_path / "ws"

    result = materialize_template(workspace, template, _inputs())

    assert list(result.materialized_files) == sorted(result.materialized_files)
    assert "pyproject.toml" in result.materialized_files
    assert "src/add_subtract.py" in [p.replace("\\", "/") for p in result.materialized_files]


def test_materialize_creates_workspace_when_missing(tmp_path: Path) -> None:
    """`materialize_template` creates the workspace directory if it does not exist."""
    template = _make_template(tmp_path)
    workspace = tmp_path / "deep" / "ws"
    assert not workspace.exists()

    materialize_template(workspace, template, _inputs())

    assert workspace.exists()
    assert (workspace / "pyproject.toml").exists()


def test_materialize_raises_on_missing_template(tmp_path: Path) -> None:
    """A non-existent template path raises BootstrapError."""
    with pytest.raises(BootstrapError, match="Template directory"):
        materialize_template(tmp_path / "ws", tmp_path / "no-template", _inputs())


def test_materialize_returns_unknown_template_version_when_missing(tmp_path: Path) -> None:
    """If `.template_version` is missing in the template, the result reports 'unknown'."""
    template = tmp_path / "tpl"
    _write(template / "pyproject.toml", "[project]\nname='__PROJECT_NAME__'\n")
    workspace = tmp_path / "ws"

    result = materialize_template(workspace, template, _inputs())

    assert result.template_version == "unknown"


# ---------------------------------------------------------------------------
# real-fixture smoke against vars/project-template/
# ---------------------------------------------------------------------------


def test_materialize_against_real_template(tmp_path: Path) -> None:
    """The shipped vars/project-template/ materializes cleanly with a sample ticket."""
    template_root = Path(__file__).parent.parent / "vars" / "project-template"
    if not template_root.exists():
        pytest.skip("vars/project-template/ not present (running outside the repo)")
    workspace = tmp_path / "ws"

    result = materialize_template(workspace, template_root, _inputs())

    # Every shipped placeholder should have been substituted.
    pyproject = (workspace / "pyproject.toml").read_text(encoding="utf-8")
    assert "__PROJECT_" not in pyproject
    assert 'name = "add-subtract"' in pyproject
    # The entry file was renamed.
    assert (workspace / "src" / "add_subtract.py").exists()
    assert (workspace / "tests" / "test_add_subtract.py").exists()
    # CLAUDE.md is present and has the project name substituted.
    claude = (workspace / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# add-subtract" in claude
    assert "__PROJECT_" not in claude
    # Template version captured from the file.
    assert result.template_version  # non-empty


def test_bootstrap_inputs_is_immutable() -> None:
    """BootstrapInputs is a frozen dataclass."""
    inputs = _inputs()
    try:
        inputs.project_name = "other"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    msg = "BootstrapInputs should be immutable"
    raise AssertionError(msg)
