"""Tests for the canonical PR body builder (FR-012, E2E-024)."""

from __future__ import annotations

import re

import pytest

from phases.pr_template import (
    BLOCKED_LABEL,
    BLOCKED_TITLE_PREFIX,
    AcceptanceCriterion,
    AttemptedApproach,
    PrTemplateInputs,
    TestReference,
    build_pr_body,
    section_headers_in_order,
)

# E2E-024 regex: section headers must appear in order, separated by content.
_HAPPY_REGEX = re.compile(
    r"^## User Story\n.*\n## Acceptance Criteria\n.*\n## Approach\n.*\n## E2E Tests\n.*\n## Notable Decisions\n.*\n## Out of Scope\n.*$",
    re.DOTALL,
)

_BLOCKED_REGEX = re.compile(
    r"^## User Story\n.*\n## Acceptance Criteria\n.*\n## Attempted Approaches\n.*\n## E2E Tests\n.*\n## Notable Decisions\n.*\n## Out of Scope\n.*\n## Proposed Decomposition\n.*$",
    re.DOTALL,
)


def _happy_inputs() -> PrTemplateInputs:
    return PrTemplateInputs(
        ticket_reference="tickets/add-subtract.md",
        summary="Add subtract function to calc module",
        acceptance_criteria=(
            AcceptanceCriterion(label="AC-1", text="subtract two ints", passed=True),
            AcceptanceCriterion(label="AC-2", text="handles zero", passed=True),
        ),
        approach="Added subtract in src/calc.py mirroring the add function. Single PR, ~30 LoC.",
        e2e_tests=(
            TestReference(pytest_path="tests/test_calc.py::test_basic", acs=("AC-1",)),
            TestReference(pytest_path="tests/test_calc.py::test_zero", acs=("AC-2",)),
        ),
        notable_decisions="Kept add and subtract symmetric; no separate sign helper.",
        out_of_scope="Multiplication and division remain follow-up tickets.",
    )


def _blocked_inputs() -> PrTemplateInputs:
    base = _happy_inputs()
    return PrTemplateInputs(
        ticket_reference=base.ticket_reference,
        summary=base.summary,
        acceptance_criteria=(
            AcceptanceCriterion(label="AC-1", text="subtract two ints", passed=True),
            AcceptanceCriterion(label="AC-2", text="handles zero", passed=False),
        ),
        approach=base.approach,
        e2e_tests=base.e2e_tests,
        notable_decisions=base.notable_decisions,
        out_of_scope=base.out_of_scope,
        attempted_approaches=(
            AttemptedApproach(name="bitwise", why_failed="overflowed for negatives"),
            AttemptedApproach(name="recursive", why_failed="exceeded depth on edge inputs"),
            AttemptedApproach(name="lookup", why_failed="memory blew up beyond 1000"),
        ),
        proposed_decomposition="Split AC-2 into a dedicated ticket: handle zero / underflow.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────────────


def test_build_pr_body_happy_path_matches_canonical_regex() -> None:
    """A happy-path body matches the canonical E2E-024 regex."""
    out = build_pr_body(_happy_inputs(), title="Add subtract")
    assert _HAPPY_REGEX.match(out.body), out.body


def test_build_pr_body_happy_path_metadata() -> None:
    """Happy-path metadata: not blocked, not draft, no labels, plain title."""
    out = build_pr_body(_happy_inputs(), title="Add subtract")
    assert out.title == "Add subtract"
    assert not out.is_blocked
    assert not out.draft
    assert out.labels == ()


def test_build_pr_body_includes_ticket_reference_and_summary() -> None:
    """The User Story line cites the ticket reference and the one-line summary."""
    out = build_pr_body(_happy_inputs(), title="x")
    assert "tickets/add-subtract.md" in out.body
    assert "Add subtract function" in out.body


def test_build_pr_body_acceptance_criteria_use_checked_boxes_when_passed() -> None:
    """All-passed ACs render as `[x]`."""
    out = build_pr_body(_happy_inputs(), title="x")
    assert "- [x] AC-1: subtract two ints" in out.body
    assert "- [x] AC-2: handles zero" in out.body
    assert "[ ]" not in out.body


def test_build_pr_body_e2e_tests_use_pytest_path_notation() -> None:
    """The E2E Tests section uses `path::function` and AC labels."""
    out = build_pr_body(_happy_inputs(), title="x")
    assert "`tests/test_calc.py::test_basic` validates AC-1" in out.body
    assert "`tests/test_calc.py::test_zero` validates AC-2" in out.body


# ──────────────────────────────────────────────────────────────────────────────
# Blocked path
# ──────────────────────────────────────────────────────────────────────────────


def test_build_pr_body_blocked_matches_blocked_regex() -> None:
    """A blocked body has the `Attempted Approaches` and `Proposed Decomposition` headers."""
    out = build_pr_body(_blocked_inputs(), title="Add subtract")
    assert _BLOCKED_REGEX.match(out.body), out.body


def test_build_pr_body_blocked_metadata() -> None:
    """Blocked: title prefix, draft=True, agent-impl-blocked label."""
    out = build_pr_body(_blocked_inputs(), title="Add subtract")
    assert out.title == BLOCKED_TITLE_PREFIX + "Add subtract"
    assert out.is_blocked
    assert out.draft
    assert BLOCKED_LABEL in out.labels


def test_build_pr_body_blocked_unchecks_failing_acs() -> None:
    """ACs whose tests still fail render as `[ ]`."""
    out = build_pr_body(_blocked_inputs(), title="x")
    assert "- [x] AC-1" in out.body
    assert "- [ ] AC-2" in out.body


def test_build_pr_body_blocked_lists_each_attempt() -> None:
    """Every attempted approach appears as a bullet with its failure reason."""
    out = build_pr_body(_blocked_inputs(), title="x")
    assert "**bitwise**: overflowed" in out.body
    assert "**recursive**: exceeded depth" in out.body
    assert "**lookup**: memory" in out.body


def test_build_pr_body_blocked_includes_decomposition() -> None:
    """The blocked PR carries a `## Proposed Decomposition` section."""
    out = build_pr_body(_blocked_inputs(), title="x")
    assert "## Proposed Decomposition" in out.body
    assert "Split AC-2" in out.body


# ──────────────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────────────


def test_build_pr_body_rejects_empty_summary() -> None:
    """An empty summary is rejected."""
    inputs = _happy_inputs()
    bad = PrTemplateInputs(
        ticket_reference=inputs.ticket_reference,
        summary=" ",
        acceptance_criteria=inputs.acceptance_criteria,
        approach=inputs.approach,
        e2e_tests=inputs.e2e_tests,
        notable_decisions=inputs.notable_decisions,
        out_of_scope=inputs.out_of_scope,
    )
    with pytest.raises(ValueError, match="summary"):
        build_pr_body(bad, title="x")


def test_build_pr_body_rejects_empty_acceptance_criteria() -> None:
    """An empty acceptance_criteria tuple is rejected."""
    inputs = _happy_inputs()
    bad = PrTemplateInputs(
        ticket_reference=inputs.ticket_reference,
        summary=inputs.summary,
        acceptance_criteria=(),
        approach=inputs.approach,
        e2e_tests=inputs.e2e_tests,
        notable_decisions=inputs.notable_decisions,
        out_of_scope=inputs.out_of_scope,
    )
    with pytest.raises(ValueError, match="acceptance_criteria"):
        build_pr_body(bad, title="x")


def test_build_pr_body_rejects_empty_approach_in_happy_mode() -> None:
    """Happy-path mode requires a non-empty approach."""
    inputs = _happy_inputs()
    bad = PrTemplateInputs(
        ticket_reference=inputs.ticket_reference,
        summary=inputs.summary,
        acceptance_criteria=inputs.acceptance_criteria,
        approach="   ",
        e2e_tests=inputs.e2e_tests,
        notable_decisions=inputs.notable_decisions,
        out_of_scope=inputs.out_of_scope,
    )
    with pytest.raises(ValueError, match="approach"):
        build_pr_body(bad, title="x")


def test_build_pr_body_rejects_blocked_without_decomposition() -> None:
    """Blocked mode requires a non-empty proposed_decomposition."""
    inputs = _blocked_inputs()
    bad = PrTemplateInputs(
        ticket_reference=inputs.ticket_reference,
        summary=inputs.summary,
        acceptance_criteria=inputs.acceptance_criteria,
        approach=inputs.approach,
        e2e_tests=inputs.e2e_tests,
        notable_decisions=inputs.notable_decisions,
        out_of_scope=inputs.out_of_scope,
        attempted_approaches=inputs.attempted_approaches,
        proposed_decomposition="",
    )
    with pytest.raises(ValueError, match="proposed_decomposition"):
        build_pr_body(bad, title="x")


def test_section_headers_in_order_returns_canonical_sequences() -> None:
    """The helper returns the exact expected headers for both modes."""
    happy = section_headers_in_order(blocked=False)
    assert happy == (
        "## User Story",
        "## Acceptance Criteria",
        "## Approach",
        "## E2E Tests",
        "## Notable Decisions",
        "## Out of Scope",
    )
    blocked = section_headers_in_order(blocked=True)
    assert blocked[2] == "## Attempted Approaches"
    assert blocked[-1] == "## Proposed Decomposition"
