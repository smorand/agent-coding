"""Definition of Ready (DoR) validation logic (FR-004, Appendix A of the spec).

Validates a ticket Markdown file against the canonical structure defined by
`vars/ticket-template/`. Pure functions; no I/O beyond reading the ticket
file path passed by the caller. The phase wrapper (`phases.dor`) handles
orchestration, comment writing, and persistence of the report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path

DESCRIPTION_MIN_CHARS = 50
TITLE_MIN_CHARS = 5
TITLE_MAX_CHARS = 80
AC_MIN_CHARS = 10

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
AC_BULLET_PATTERN = re.compile(r"^-\s+AC-(\d+):\s*(.*)$")
INFRA_REQUIRES_PATTERN = re.compile(r"^-\s+requires:\s*(.*)$")
FRONTMATTER_DELIMITER = "---"

REQUIRED_SECTIONS = ("Description", "Acceptance Criteria")


class DorStatus(StrEnum):
    """Top-level DoR verdict."""

    READY = "READY"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class FieldIssue:
    """One missing or insufficient field on a ticket."""

    field: str
    reason: str


@dataclass(frozen=True)
class DorReport:
    """Aggregate result of a DoR check."""

    status: DorStatus
    issues: tuple[FieldIssue, ...]
    generated_at: datetime


def validate_ticket(ticket_path: Path) -> DorReport:
    """Validate `ticket_path` against the canonical ticket template.

    Returns a `DorReport`. Status is READY when `issues` is empty, NOT_READY
    otherwise. The function never raises on validation failures; it raises
    only on real I/O errors (file not found, permission denied).
    """
    now = datetime.now(UTC)
    issues: list[FieldIssue] = []

    if ticket_path.suffix != ".md":
        issues.append(FieldIssue("file extension", "Ticket file must be a .md (Markdown) file"))
        return DorReport(status=DorStatus.NOT_READY, issues=tuple(issues), generated_at=now)

    text = ticket_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    issues.extend(_validate_frontmatter(frontmatter))
    issues.extend(_validate_sections(body))

    status = DorStatus.READY if not issues else DorStatus.NOT_READY
    return DorReport(status=status, issues=tuple(issues), generated_at=now)


def format_dor_comment(report: DorReport, agent_version: str) -> str:
    """Render the canonical DoR comment (Appendix A.4 of the spec)."""
    bullets = "\n".join(f"- **{issue.field}**: {issue.reason}" for issue in report.issues)
    if not bullets:
        bullets = "- (no specific issue listed)"
    return (
        "<!-- agent-code DoR report; DO NOT EDIT below this line -->\n"
        "\n"
        "## DoR Report (agent-code)\n"
        "\n"
        f"**Status**: {report.status.value}\n"
        f"**Generated at**: {report.generated_at.isoformat()}\n"
        f"**Agent version**: {agent_version}\n"
        "\n"
        "### Missing or insufficient fields\n"
        "\n"
        f"{bullets}\n"
        "\n"
        "### How to proceed\n"
        "\n"
        "Edit this ticket to address the points above, then re-trigger `agent-code`.\n"
        "\n"
        "<!-- end agent-code DoR report -->\n"
    )


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split YAML frontmatter from body. Returns (frontmatter_text or None, body)."""
    if not text.startswith(FRONTMATTER_DELIMITER + "\n") and not text.startswith(FRONTMATTER_DELIMITER + "\r\n"):
        return None, text
    rest = text.split("\n", 1)[1] if "\n" in text else ""
    end_idx = rest.find("\n" + FRONTMATTER_DELIMITER)
    if end_idx == -1:
        return None, text
    frontmatter = rest[:end_idx]
    body_start = end_idx + len("\n" + FRONTMATTER_DELIMITER)
    body = rest[body_start:].lstrip("\n")
    return frontmatter, body


def _validate_frontmatter(frontmatter: str | None) -> list[FieldIssue]:
    issues: list[FieldIssue] = []
    if frontmatter is None:
        issues.append(FieldIssue("frontmatter", "YAML frontmatter (between `---` lines) is missing"))
        return issues
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        issues.append(FieldIssue("frontmatter", f"YAML parse error: {exc}"))
        return issues
    if not isinstance(data, dict):
        issues.append(FieldIssue("frontmatter", "must be a YAML mapping (key: value pairs)"))
        return issues
    issues.extend(_validate_id(data.get("id")))
    issues.extend(_validate_title(data.get("title")))
    return issues


def _validate_id(value: object) -> list[FieldIssue]:
    if value is None:
        return [FieldIssue("id", "frontmatter `id` is missing")]
    if not isinstance(value, str):
        return [FieldIssue("id", "frontmatter `id` must be a string slug")]
    if not ID_PATTERN.match(value):
        return [
            FieldIssue(
                "id",
                "must match `^[a-z0-9][a-z0-9-]{2,63}$` (lowercase slug, 3 to 64 chars)",
            )
        ]
    return []


def _validate_title(value: object) -> list[FieldIssue]:
    if value is None:
        return [FieldIssue("title", "frontmatter `title` is missing")]
    if not isinstance(value, str):
        return [FieldIssue("title", "frontmatter `title` must be a string")]
    stripped = value.strip()
    if len(stripped) < TITLE_MIN_CHARS or len(stripped) > TITLE_MAX_CHARS:
        return [
            FieldIssue(
                "title",
                f"must be {TITLE_MIN_CHARS} to {TITLE_MAX_CHARS} chars (got {len(stripped)})",
            )
        ]
    return []


def _validate_sections(body: str) -> list[FieldIssue]:
    sections = _parse_sections(body)
    issues: list[FieldIssue] = []
    for required in REQUIRED_SECTIONS:
        if required not in sections:
            issues.append(FieldIssue(required, "section is missing"))
    if "Description" in sections:
        issues.extend(_validate_description(sections["Description"]))
    if "Acceptance Criteria" in sections:
        issues.extend(_validate_acceptance_criteria(sections["Acceptance Criteria"]))
    if "Infrastructure" in sections:
        issues.extend(_validate_infrastructure(sections["Infrastructure"]))
    return issues


def _parse_sections(body: str) -> dict[str, str]:
    """Return a mapping of section title to its body text."""
    sections: dict[str, str] = {}
    matches = list(SECTION_PATTERN.finditer(body))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        # Strip optional "(REQUIRED)" annotation appended after the section title.
        clean_title = re.sub(r"\s*\(REQUIRED\)\s*$", "", title)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[clean_title] = body[start:end].strip()
    return sections


def _validate_description(content: str) -> list[FieldIssue]:
    significant = re.sub(r"\s+", " ", content).strip()
    if len(significant) < DESCRIPTION_MIN_CHARS:
        return [
            FieldIssue(
                "Description",
                f"body has {len(significant)} non-whitespace chars; need >= {DESCRIPTION_MIN_CHARS}",
            )
        ]
    return []


def _validate_acceptance_criteria(content: str) -> list[FieldIssue]:
    issues: list[FieldIssue] = []
    bullets: list[tuple[int, str]] = []
    for line in content.splitlines():
        match = AC_BULLET_PATTERN.match(line.strip())
        if match:
            number = int(match.group(1))
            text = match.group(2).strip()
            bullets.append((number, text))
    if not bullets:
        issues.append(
            FieldIssue(
                "Acceptance Criteria",
                "section is empty; at least one bullet of the form `- AC-N: <criterion>` is required",
            )
        )
        return issues
    for number, text in bullets:
        if len(re.sub(r"\s+", " ", text)) < AC_MIN_CHARS:
            issues.append(
                FieldIssue(
                    f"AC-{number}",
                    f"criterion text has fewer than {AC_MIN_CHARS} non-whitespace chars",
                )
            )
    return issues


def _validate_infrastructure(content: str) -> list[FieldIssue]:
    issues: list[FieldIssue] = []
    for line in content.splitlines():
        match = INFRA_REQUIRES_PATTERN.match(line.strip())
        if match and not match.group(1).strip():
            issues.append(
                FieldIssue(
                    "Infrastructure",
                    "`requires:` line has empty value",
                )
            )
    return issues
