"""Pull Request body builder (FR-012, E2E-024).

Pure functions that build the canonical PR body string from structured
inputs. The output matches `.agent_docs/pr-template.md` literally so the
reviewer agent can verify it with a regex (E2E-024 requires the exact
section headers in order).

Two modes:
- "happy path": all ACs pass; `Approach` section is populated.
- "blocked": the implementation loop exhausted approaches; the title is
  prefixed with `[agent-impl-blocked]`, ACs whose tests still fail are
  unchecked, the `Approach` section becomes `Attempted Approaches` with
  one bullet per attempt, and a `Proposed Decomposition` section is
  appended.

The function does NOT call `gh`; it produces the string. The caller
hands the result to `GhPrCreateTool`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

BLOCKED_LABEL = "agent-impl-blocked"
BLOCKED_TITLE_PREFIX = "[agent-impl-blocked] "

EMPTY_SECTION_PLACEHOLDER = "_None._"


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One acceptance criterion with its pass status."""

    label: str  # e.g. "AC-1"
    text: str
    passed: bool = True


@dataclass(frozen=True)
class TestReference:
    """One E2E test mapped to one or more ACs."""

    pytest_path: str  # e.g. "tests/test_subtract.py::test_basic"
    acs: tuple[str, ...]  # e.g. ("AC-1",) or ("AC-1", "AC-3")


@dataclass(frozen=True)
class AttemptedApproach:
    """One approach attempted by the implementation loop (blocked PR only)."""

    name: str
    why_failed: str


@dataclass(frozen=True)
class PrTemplateInputs:
    """Inputs to `build_pr_body`. Frozen for testability."""

    ticket_reference: str  # path or URL of the ticket
    summary: str  # one-line summary
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    approach: str  # 3-5 lines, ignored when `attempted_approaches` is set
    e2e_tests: tuple[TestReference, ...]
    notable_decisions: str
    out_of_scope: str
    # Blocked-only fields:
    attempted_approaches: tuple[AttemptedApproach, ...] = ()
    proposed_decomposition: str = ""


@dataclass(frozen=True)
class PrTemplateOutputs:
    """Result returned by `build_pr_body`."""

    title: str
    body: str
    is_blocked: bool
    labels: tuple[str, ...]
    draft: bool


def build_pr_body(inputs: PrTemplateInputs, *, title: str) -> PrTemplateOutputs:
    """Build the canonical PR title, body, and metadata.

    Validates that every required section has content; raises ValueError
    on empty sections (matches the spec's "no empty sections" rule).
    The `is_blocked` flag is implied from `attempted_approaches`.
    """
    blocked = bool(inputs.attempted_approaches)
    _validate_inputs(inputs, blocked=blocked)
    body = _render_body(inputs, blocked=blocked)
    final_title = (BLOCKED_TITLE_PREFIX + title) if blocked else title
    labels: tuple[str, ...] = (BLOCKED_LABEL,) if blocked else ()
    return PrTemplateOutputs(
        title=final_title,
        body=body,
        is_blocked=blocked,
        labels=labels,
        draft=blocked,
    )


def _validate_inputs(inputs: PrTemplateInputs, *, blocked: bool) -> None:
    if not inputs.ticket_reference.strip():
        msg = "ticket_reference must be non-empty"
        raise ValueError(msg)
    if not inputs.summary.strip():
        msg = "summary must be non-empty"
        raise ValueError(msg)
    if not inputs.acceptance_criteria:
        msg = "acceptance_criteria must contain at least one AC"
        raise ValueError(msg)
    if not inputs.e2e_tests:
        msg = "e2e_tests must contain at least one test reference"
        raise ValueError(msg)
    if not inputs.notable_decisions.strip():
        msg = "notable_decisions must be non-empty"
        raise ValueError(msg)
    if not inputs.out_of_scope.strip():
        msg = "out_of_scope must be non-empty"
        raise ValueError(msg)
    if blocked:
        if not inputs.proposed_decomposition.strip():
            msg = "proposed_decomposition is required when attempted_approaches is set"
            raise ValueError(msg)
    elif not inputs.approach.strip():
        msg = "approach must be non-empty in happy-path mode"
        raise ValueError(msg)


def _render_body(inputs: PrTemplateInputs, *, blocked: bool) -> str:
    sections: list[str] = []
    sections.append(_render_user_story(inputs))
    sections.append(_render_acceptance_criteria(inputs))
    sections.append(_render_approach(inputs, blocked=blocked))
    sections.append(_render_e2e_tests(inputs))
    sections.append(_render_notable_decisions(inputs))
    sections.append(_render_out_of_scope(inputs))
    if blocked:
        sections.append(_render_proposed_decomposition(inputs))
    return "\n\n".join(sections) + "\n"


def _render_user_story(inputs: PrTemplateInputs) -> str:
    return f"## User Story\n{inputs.ticket_reference}; {inputs.summary.strip()}"


def _render_acceptance_criteria(inputs: PrTemplateInputs) -> str:
    lines = ["## Acceptance Criteria"]
    for ac in inputs.acceptance_criteria:
        mark = "[x]" if ac.passed else "[ ]"
        lines.append(f"- {mark} {ac.label}: {ac.text}")
    return "\n".join(lines)


def _render_approach(inputs: PrTemplateInputs, *, blocked: bool) -> str:
    if blocked:
        lines = ["## Attempted Approaches"]
        for attempt in inputs.attempted_approaches:
            lines.append(f"- **{attempt.name}**: {attempt.why_failed}")
        return "\n".join(lines)
    return f"## Approach\n{inputs.approach.strip()}"


def _render_e2e_tests(inputs: PrTemplateInputs) -> str:
    lines = ["## E2E Tests"]
    for test in inputs.e2e_tests:
        ac_label = ", ".join(test.acs) if test.acs else "no AC"
        lines.append(f"- `{test.pytest_path}` validates {ac_label}")
    return "\n".join(lines)


def _render_notable_decisions(inputs: PrTemplateInputs) -> str:
    return f"## Notable Decisions\n{inputs.notable_decisions.strip()}"


def _render_out_of_scope(inputs: PrTemplateInputs) -> str:
    return f"## Out of Scope\n{inputs.out_of_scope.strip()}"


def _render_proposed_decomposition(inputs: PrTemplateInputs) -> str:
    return f"## Proposed Decomposition\n{inputs.proposed_decomposition.strip()}"


def section_headers_in_order(blocked: bool) -> Sequence[str]:
    """Return the canonical section headers, in order, for verification (E2E-024)."""
    base = [
        "## User Story",
        "## Acceptance Criteria",
        "## Attempted Approaches" if blocked else "## Approach",
        "## E2E Tests",
        "## Notable Decisions",
        "## Out of Scope",
    ]
    if blocked:
        base.append("## Proposed Decomposition")
    return tuple(base)
