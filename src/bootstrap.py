"""Bootstrap an empty workspace from the canonical Python project template (FR-014).

When the classification phase detects an empty workspace (only `.git/` and a
ticket file), this module materializes `vars/project-template/` into the
workspace, substituting placeholders from the ticket. Pure copy + substitute
+ rename; `uv sync` and the initial git commit are left to the operator (or
to a follow-up phase that wires the tool registry).

Substitution placeholders (consumed in every text file copied):

| Placeholder | Source |
|---|---|
| `__PROJECT_NAME__` | ticket frontmatter `id` (kebab-case) |
| `__PROJECT_ENTRY__` | snake_case of project name |
| `__PROJECT_DESCRIPTION__` | first paragraph of ticket Description section |
| `__PROJECT_AUTHOR__` | ticket frontmatter `author` (or fallback) |
| `__PROJECT_AUTHOR_EMAIL__` | not in ticket; fallback supplied by caller |
| `__PROJECT_YEAR__` | current year |
| `__PROJECT_PREFIX_UPPER__` | upper-snake of project name with trailing underscore |

Files whose name contains `__PROJECT_ENTRY__` (e.g., `src/__PROJECT_ENTRY__.py`)
are renamed at materialization time.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

TEMPLATE_VERSION_FILENAME = ".template_version"
DEFAULT_TEMPLATE_VERSION = "unknown"
DEFAULT_AUTHOR = "Unknown"
DEFAULT_AUTHOR_EMAIL = "unknown@example.com"

ENTRY_PLACEHOLDER = "__PROJECT_ENTRY__"
NAME_PLACEHOLDER = "__PROJECT_NAME__"
DESCRIPTION_PLACEHOLDER = "__PROJECT_DESCRIPTION__"
AUTHOR_PLACEHOLDER = "__PROJECT_AUTHOR__"
AUTHOR_EMAIL_PLACEHOLDER = "__PROJECT_AUTHOR_EMAIL__"
YEAR_PLACEHOLDER = "__PROJECT_YEAR__"
PREFIX_UPPER_PLACEHOLDER = "__PROJECT_PREFIX_UPPER__"

FRONTMATTER_DELIMITER = "---"
SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class BootstrapError(Exception):
    """Raised when bootstrap cannot proceed (template missing, ticket invalid, etc.)."""


@dataclass(frozen=True)
class BootstrapInputs:
    """Substitution values derived from the ticket (or fallbacks)."""

    project_name: str  # kebab-case slug, used for __PROJECT_NAME__
    project_entry: str  # snake_case, used for __PROJECT_ENTRY__
    project_description: str
    project_author: str
    project_author_email: str
    project_year: int

    @property
    def project_prefix_upper(self) -> str:
        """Upper-snake-case env-var prefix with trailing underscore."""
        return self.project_entry.upper() + "_"


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of `materialize_template`."""

    template_version: str
    materialized_files: tuple[str, ...]  # workspace-relative paths


def extract_inputs_from_ticket(
    ticket_path: Path,
    *,
    fallback_author: str = DEFAULT_AUTHOR,
    fallback_email: str = DEFAULT_AUTHOR_EMAIL,
    now: datetime | None = None,
) -> BootstrapInputs:
    """Parse the ticket and produce the substitution inputs.

    Reads YAML frontmatter and the first paragraph of the Description
    section. Raises `BootstrapError` if mandatory fields are missing.
    """
    if not ticket_path.exists():
        msg = f"Ticket file not found: {ticket_path}"
        raise BootstrapError(msg)
    text = ticket_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        msg = "Ticket has no YAML frontmatter; cannot derive project name"
        raise BootstrapError(msg)
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        msg = f"Ticket frontmatter is not valid YAML: {exc}"
        raise BootstrapError(msg) from exc
    if not isinstance(data, dict):
        msg = "Ticket frontmatter must be a YAML mapping"
        raise BootstrapError(msg)

    raw_id = data.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        msg = "Ticket frontmatter `id` is missing or not a string"
        raise BootstrapError(msg)
    project_entry = raw_id.replace("-", "_")
    description = _extract_description_paragraph(body)
    author_value = data.get("author")
    author = author_value if isinstance(author_value, str) and author_value else fallback_author
    timestamp = now if now is not None else datetime.now(UTC)
    return BootstrapInputs(
        project_name=raw_id,
        project_entry=project_entry,
        project_description=description,
        project_author=author,
        project_author_email=fallback_email,
        project_year=timestamp.year,
    )


def materialize_template(
    workspace: Path,
    template_path: Path,
    inputs: BootstrapInputs,
) -> BootstrapResult:
    """Copy `template_path` into `workspace`, substitute placeholders, rename entry files.

    Returns a `BootstrapResult` listing every materialized path (relative to
    workspace). Raises `BootstrapError` if the template directory is missing.
    """
    if not template_path.exists() or not template_path.is_dir():
        msg = f"Template directory not found: {template_path}"
        raise BootstrapError(msg)

    workspace.mkdir(parents=True, exist_ok=True)
    template_version = _read_template_version(template_path)
    substitutions = _build_substitutions(inputs)
    materialized: list[str] = []
    for source in _walk_files(template_path):
        relative = source.relative_to(template_path)
        target_relative = _substitute_path(relative, inputs.project_entry)
        target = workspace / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if _looks_text(source):
            content = source.read_text(encoding="utf-8")
            for placeholder, value in substitutions.items():
                content = content.replace(placeholder, value)
            target.write_text(content, encoding="utf-8")
        else:
            shutil.copyfile(source, target)
        materialized.append(str(target_relative))
    materialized.sort()
    logger.info(
        "Bootstrap materialized %d files into %s (template v%s)",
        len(materialized),
        workspace,
        template_version,
    )
    return BootstrapResult(template_version=template_version, materialized_files=tuple(materialized))


def _build_substitutions(inputs: BootstrapInputs) -> dict[str, str]:
    return {
        NAME_PLACEHOLDER: inputs.project_name,
        ENTRY_PLACEHOLDER: inputs.project_entry,
        DESCRIPTION_PLACEHOLDER: inputs.project_description,
        AUTHOR_PLACEHOLDER: inputs.project_author,
        AUTHOR_EMAIL_PLACEHOLDER: inputs.project_author_email,
        YEAR_PLACEHOLDER: str(inputs.project_year),
        PREFIX_UPPER_PLACEHOLDER: inputs.project_prefix_upper,
    }


def _walk_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def _substitute_path(relative: Path, project_entry: str) -> Path:
    parts = [part.replace(ENTRY_PLACEHOLDER, project_entry) for part in relative.parts]
    return Path(*parts)


def _read_template_version(template_path: Path) -> str:
    candidate = template_path / TEMPLATE_VERSION_FILENAME
    if not candidate.exists():
        return DEFAULT_TEMPLATE_VERSION
    return candidate.read_text(encoding="utf-8").strip() or DEFAULT_TEMPLATE_VERSION


def _looks_text(path: Path) -> bool:
    """Best-effort text/binary discrimination by extension and small read."""
    binary_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".so", ".dylib"}
    if path.suffix.lower() in binary_suffixes:
        return False
    try:
        sample = path.read_bytes()[:1024]
    except OSError:
        return False
    return b"\x00" not in sample


def _split_frontmatter(text: str) -> tuple[str | None, str]:
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


def _extract_description_paragraph(body: str) -> str:
    """Return the first non-empty paragraph of the `## Description` section.

    Falls back to a generic placeholder if the section is missing or empty.
    """
    sections = _parse_sections(body)
    description_text = sections.get("Description", "").strip()
    if not description_text:
        return "Project bootstrapped from the agent-code Python template."
    paragraphs = re.split(r"\n\s*\n", description_text)
    for paragraph in paragraphs:
        cleaned = re.sub(r"\s+", " ", paragraph).strip()
        if cleaned:
            return cleaned
    return "Project bootstrapped from the agent-code Python template."


def _parse_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(SECTION_PATTERN.finditer(body))
    for index, match in enumerate(matches):
        title = re.sub(r"\s*\(REQUIRED\)\s*$", "", match.group(1).strip())
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections
