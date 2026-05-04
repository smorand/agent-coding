"""Tests for the Definition of Ready validator (FR-004)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from phases.dor_validator import (
    DorStatus,
    format_dor_comment,
    validate_ticket,
)

READY_TICKET = """\
---
id: add-subtract
title: Add a subtract function to calc
created: 2026-05-04
author: smorand
---

# Add a subtract function to calc

## Description

The calc module currently exposes only `add`. We need a symmetric `subtract`
function for our internal arithmetic helpers, callable from any module that
imports `calc`.

## Acceptance Criteria

- AC-1: `calc.subtract(5, 3)` returns the integer `2`.
- AC-2: `calc.subtract(0, 0)` returns the integer `0`.
- AC-3: `calc.subtract` raises `TypeError` on non-integer input with a clear message.
"""


def _write_ticket(tmp_path: Path, content: str, name: str = "ticket.md") -> Path:
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return target


def test_validate_ready_ticket_returns_ready(tmp_path: Path) -> None:
    """A complete ticket passes validation with no issues."""
    path = _write_ticket(tmp_path, READY_TICKET)
    report = validate_ticket(path)
    assert report.status == DorStatus.READY
    assert report.issues == ()


def test_non_md_extension_is_rejected(tmp_path: Path) -> None:
    """A ticket file without .md extension is rejected immediately."""
    path = _write_ticket(tmp_path, READY_TICKET, name="ticket.txt")
    report = validate_ticket(path)
    assert report.status == DorStatus.NOT_READY
    assert any(i.field == "file extension" for i in report.issues)


def test_missing_frontmatter_is_rejected(tmp_path: Path) -> None:
    """A ticket without YAML frontmatter is rejected."""
    path = _write_ticket(tmp_path, "# title\n\n## Description\n\n" + ("x" * 60))
    report = validate_ticket(path)
    assert any(i.field == "frontmatter" for i in report.issues)


def test_invalid_yaml_frontmatter_is_rejected(tmp_path: Path) -> None:
    """Malformed YAML in frontmatter is reported with a YAML parse error."""
    body = "---\nid: [unclosed\n---\n\n## Description\n\n" + ("x" * 60)
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    issues = [i for i in report.issues if i.field == "frontmatter"]
    assert issues
    assert "YAML parse error" in issues[0].reason


def test_frontmatter_must_be_mapping(tmp_path: Path) -> None:
    """A scalar at the top level of frontmatter is rejected."""
    body = "---\njust-a-string\n---\n\n## Description\n\n" + ("x" * 60)
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "frontmatter" and "mapping" in i.reason for i in report.issues)


def test_missing_id_is_rejected(tmp_path: Path) -> None:
    """A ticket missing `id` in frontmatter is rejected."""
    body = (
        "---\ntitle: A reasonable title\n---\n\n## Description\n\n"
        + ("x" * 60)
        + "\n\n## Acceptance Criteria\n\n- AC-1: lots of text here.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "id" and "missing" in i.reason for i in report.issues)


@pytest.mark.parametrize(
    "bad_id",
    ["X", "ab", "Foo", "foo_bar", "-foo", "0", "really-long-" + "x" * 80],
)
def test_invalid_id_pattern_is_rejected(tmp_path: Path, bad_id: str) -> None:
    """An `id` that fails the slug pattern is rejected."""
    body = (
        f"---\nid: {bad_id}\ntitle: A reasonable title\n---\n\n"
        "## Description\n\n" + ("x" * 60) + "\n\n## Acceptance Criteria\n\n- AC-1: lots of text.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "id" for i in report.issues)


def test_missing_title_is_rejected(tmp_path: Path) -> None:
    """A ticket missing `title` is rejected."""
    body = (
        "---\nid: valid-slug\n---\n\n## Description\n\n"
        + ("x" * 60)
        + "\n\n## Acceptance Criteria\n\n- AC-1: lots of text.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "title" for i in report.issues)


@pytest.mark.parametrize("title_len", [0, 4, 81, 200])
def test_title_length_bounds(tmp_path: Path, title_len: int) -> None:
    """Title must be 5..80 chars."""
    title = "a" * title_len
    body = (
        f"---\nid: valid-slug\ntitle: {title}\n---\n\n"
        "## Description\n\n" + ("x" * 60) + "\n\n## Acceptance Criteria\n\n- AC-1: text long enough.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "title" for i in report.issues)


def test_missing_description_section_is_rejected(tmp_path: Path) -> None:
    """A ticket missing the Description section is rejected."""
    body = "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n## Acceptance Criteria\n\n- AC-1: text long enough.\n"
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "Description" and "missing" in i.reason for i in report.issues)


def test_short_description_is_rejected(tmp_path: Path) -> None:
    """A Description body shorter than 50 chars is rejected."""
    body = (
        "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n"
        "## Description\n\nshort.\n\n## Acceptance Criteria\n\n- AC-1: long enough text.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "Description" and "non-whitespace" in i.reason for i in report.issues)


def test_required_annotation_is_stripped(tmp_path: Path) -> None:
    """A `## Description (REQUIRED)` header still maps to the Description section."""
    body = (
        "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n"
        "## Description (REQUIRED)\n\n"
        + ("x" * 60)
        + "\n\n## Acceptance Criteria (REQUIRED)\n\n- AC-1: long enough text here.\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert report.status == DorStatus.READY


def test_missing_acceptance_criteria_section_is_rejected(tmp_path: Path) -> None:
    """A ticket missing the Acceptance Criteria section is rejected."""
    body = "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n## Description\n\n" + ("x" * 60) + "\n"
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "Acceptance Criteria" and "missing" in i.reason for i in report.issues)


def test_empty_acceptance_criteria_section_is_rejected(tmp_path: Path) -> None:
    """An Acceptance Criteria section with no AC bullets is rejected."""
    body = (
        "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n"
        "## Description\n\n" + ("x" * 60) + "\n\n## Acceptance Criteria\n\n(no bullets)\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "Acceptance Criteria" and "empty" in i.reason for i in report.issues)


def test_short_ac_text_is_rejected(tmp_path: Path) -> None:
    """An AC bullet whose criterion text is too short is rejected per-bullet."""
    body = (
        "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n"
        "## Description\n\n" + ("x" * 60) + "\n\n## Acceptance Criteria\n\n"
        "- AC-1: ok long enough.\n- AC-2: short\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    fields = {i.field for i in report.issues}
    assert "AC-2" in fields
    assert "AC-1" not in fields


def test_infrastructure_empty_requires_value_is_rejected(tmp_path: Path) -> None:
    """An Infrastructure `requires:` line with empty value is rejected."""
    body = (
        "---\nid: valid-slug\ntitle: Reasonable title\n---\n\n"
        "## Description\n\n" + ("x" * 60) + "\n\n## Acceptance Criteria\n\n- AC-1: long enough.\n\n"
        "## Infrastructure\n\n- requires: \n- requires: postgres 15\n"
    )
    path = _write_ticket(tmp_path, body)
    report = validate_ticket(path)
    assert any(i.field == "Infrastructure" for i in report.issues)


def test_format_dor_comment_includes_required_markers() -> None:
    """The rendered DoR comment includes the canonical begin/end markers and fields."""
    path_payload = """---
id: x
title: too-short-to-be-valid
---

## Description

short
"""
    # Use validate_ticket to obtain a realistic NOT_READY report.
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write(path_payload)
        tmp_path_obj = Path(tmp.name)
    try:
        report = validate_ticket(tmp_path_obj)
    finally:
        tmp_path_obj.unlink()
    rendered = format_dor_comment(report, agent_version="0.1.0")
    assert rendered.startswith("<!-- agent-code DoR report")
    assert rendered.rstrip().endswith("<!-- end agent-code DoR report -->")
    assert "**Status**: NOT_READY" in rendered
    assert "**Agent version**: 0.1.0" in rendered
    assert "### Missing or insufficient fields" in rendered
    assert "### How to proceed" in rendered


def test_validate_real_fixture_ready(tmp_path: Path) -> None:
    """The shipped vars/ticket-template/ticket-example-ready.md passes validation."""
    fixture = Path(__file__).parent.parent / "vars" / "ticket-template" / "ticket-example-ready.md"
    if not fixture.exists():
        pytest.skip("vars fixture not present (running outside the repo)")
    # Copy to tmp to keep validation hermetic.
    target = tmp_path / "ready.md"
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    report = validate_ticket(target)
    assert report.status == DorStatus.READY, report.issues


def test_validate_real_fixture_not_ready(tmp_path: Path) -> None:
    """The shipped vars/ticket-template/ticket-example-not-ready.md fails validation."""
    fixture = Path(__file__).parent.parent / "vars" / "ticket-template" / "ticket-example-not-ready.md"
    if not fixture.exists():
        pytest.skip("vars fixture not present (running outside the repo)")
    target = tmp_path / "nr.md"
    target.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")
    report = validate_ticket(target)
    assert report.status == DorStatus.NOT_READY
    fields = {i.field for i in report.issues}
    # The NOT_READY example is missing the Acceptance Criteria section and has
    # a short Description.
    assert "Acceptance Criteria" in fields
