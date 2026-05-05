"""Tests for the Pull Request creation phase (FR-012, E2E-024, E2E-026)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phases.base import OutcomeKind, PhaseContext
from phases.pr_creation import (
    PR_CREATION_REPORT_FILENAME,
    PrCreationPhase,
    _collect_e2e_tests,
    _detect_github_issue_ref,
    _extract_pr_url,
    _parse_acceptance_criteria,
)
from state import PhaseName, PhaseRecord, State
from tools.base import SubprocessOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class FakeRunner:
    """Records every argv; routes by leading words to canned outcomes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.commit_exists: bool = True
        self.pr_url: str = "https://github.com/x/y/pull/42"
        self.push_returncode: int = 0
        self.gh_returncode: int = 0

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        del cwd, timeout, input_text
        argv_list = list(argv)
        self.calls.append(argv_list)
        head = tuple(argv_list[:3])
        if head[:2] == ("git", "cat-file"):
            return SubprocessOutcome(returncode=0 if self.commit_exists else 1, stdout="", stderr="")
        if head[:2] == ("git", "push"):
            return SubprocessOutcome(
                returncode=self.push_returncode, stdout="", stderr="boom" if self.push_returncode else ""
            )
        if head[:1] == ("gh",):
            if head[1:3] == ("pr", "create"):
                return SubprocessOutcome(returncode=self.gh_returncode, stdout=self.pr_url + "\n", stderr="")
            return SubprocessOutcome(returncode=0, stdout="", stderr="")
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


def _state(*, e2e_sha: str | None = "abc123", verdict: str | None = "APPROVE") -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.PR_CREATION,
        phases=[PhaseRecord(name=PhaseName.PR_CREATION)],
        e2e_commit_sha=e2e_sha,
        review_verdict=verdict,
    )


def _make_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    work_dir = workspace / ".agent_work" / "demo"
    work_dir.mkdir(parents=True)
    ticket = workspace / "ticket.md"
    ticket.write_text(
        "---\nid: demo\ntitle: Add subtract feature\n---\n\n"
        "## Description\n\nImplement subtract.\n\n"
        "## Acceptance Criteria\n\n"
        "- AC-1: subtract two ints\n- AC-2: handles zero\n",
        encoding="utf-8",
    )
    (work_dir / "plan.md").write_text("Use subtract in src/calc.py.", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "# AC-1\ndef test_basic():\n    assert True\n\n# AC-2\ndef test_zero():\n    assert True\n",
        encoding="utf-8",
    )
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path, *, state: State) -> PhaseContext:
    return PhaseContext(state=state, work_dir=work_dir, ticket_path=str(ticket_path))


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_acceptance_criteria_extracts_labels_and_text() -> None:
    """`- AC-1: text` bullets become AcceptanceCriterion records."""
    text = "## Acceptance Criteria\n\n- AC-1: alpha\n- AC-2: beta\n"
    acs = _parse_acceptance_criteria(text, all_passed=True)
    assert [(a.label, a.text, a.passed) for a in acs] == [
        ("AC-1", "alpha", True),
        ("AC-2", "beta", True),
    ]


def test_parse_acceptance_criteria_marks_blocked() -> None:
    """When the run is blocked, all ACs render as not-passed."""
    text = "- AC-1: x\n"
    acs = _parse_acceptance_criteria(text, all_passed=False)
    assert acs[0].passed is False


def test_collect_e2e_tests_walks_tests_dir(tmp_path: Path) -> None:
    """Test functions are collected with their AC comments."""
    workspace = tmp_path
    tests = workspace / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text(
        "# AC-1, AC-3\ndef test_one():\n    pass\n\ndef test_two():\n    pass\n",
        encoding="utf-8",
    )
    refs = _collect_e2e_tests(workspace)
    assert refs[0].pytest_path == "tests/test_x.py::test_one"
    assert refs[0].acs == ("AC-1", "AC-3")
    assert refs[1].pytest_path == "tests/test_x.py::test_two"
    # No comment was found near test_two; fallback AC.
    assert refs[1].acs == ("AC-1",)


def test_extract_pr_url_finds_pull_url() -> None:
    """The first `https://.../pull/<n>` substring is returned."""
    out = "Opening browser...\nhttps://github.com/o/r/pull/77\n"
    assert _extract_pr_url(out) == "https://github.com/o/r/pull/77"


def test_extract_pr_url_returns_none_when_absent() -> None:
    """No URL → None."""
    assert _extract_pr_url("nothing here") is None


def test_detect_github_issue_ref_returns_number_when_url_present(tmp_path: Path) -> None:
    """A GitHub issues URL in the ticket yields the numeric id."""
    ticket = tmp_path / "t.md"
    ticket.write_text("ref: https://github.com/o/r/issues/9\n", encoding="utf-8")
    assert _detect_github_issue_ref(ticket) == "9"


def test_detect_github_issue_ref_returns_none_for_local_ticket(tmp_path: Path) -> None:
    """A plain local ticket has no GitHub issue ref."""
    ticket = tmp_path / "t.md"
    ticket.write_text("just a local file\n", encoding="utf-8")
    assert _detect_github_issue_ref(ticket) is None


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_happy_path_pushes_creates_pr_and_persists_report(tmp_path: Path) -> None:
    """An APPROVE state with intact SHA pushes, opens a non-draft PR, persists report."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state(verdict="APPROVE"))

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    assert ctx.state.pr_url == "https://github.com/x/y/pull/42"
    push_calls = [c for c in runner.calls if c[:2] == ["git", "push"]]
    assert len(push_calls) == 1
    pr_calls = [c for c in runner.calls if c[:3] == ["gh", "pr", "create"]]
    assert len(pr_calls) == 1
    assert "--draft" not in pr_calls[0]
    payload = json.loads((work_dir / PR_CREATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["pr_url"] == "https://github.com/x/y/pull/42"
    assert payload["is_blocked"] is False
    assert payload["is_draft"] is False


async def test_run_blocked_path_opens_draft_pr_with_label(tmp_path: Path) -> None:
    """REQUEST_CHANGES yields a draft PR with the agent-impl-blocked label."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state(verdict="REQUEST_CHANGES"))

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    pr_calls = [c for c in runner.calls if c[:3] == ["gh", "pr", "create"]]
    assert "--draft" in pr_calls[0]
    label_idxs = [i for i, a in enumerate(pr_calls[0]) if a == "--label"]
    assert any(pr_calls[0][i + 1] == "agent-impl-blocked" for i in label_idxs)
    label_create_calls = [c for c in runner.calls if c[:3] == ["gh", "label", "create"]]
    assert len(label_create_calls) == 1


async def test_run_aborts_when_e2e_commit_was_tampered(tmp_path: Path) -> None:
    """E2E-026: a missing recorded SHA halts with HALT_EXHAUSTED before any push."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    runner.commit_exists = False
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state(verdict="APPROVE", e2e_sha="cafef00d"))

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.HALT_EXHAUSTED
    assert "modified after lock" in outcome.message
    assert not any(c[:2] == ["git", "push"] for c in runner.calls)


async def test_run_returns_halt_error_on_push_failure(tmp_path: Path) -> None:
    """A failing `git push` halts the phase with HALT_ERROR."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    runner.push_returncode = 1
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state())

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "git push" in outcome.message


async def test_run_returns_halt_error_when_gh_pr_create_fails(tmp_path: Path) -> None:
    """A non-zero `gh pr create` halts the phase with HALT_ERROR."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    runner.gh_returncode = 1
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state())

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "gh pr create" in outcome.message


async def test_run_pr_body_satisfies_canonical_section_order(tmp_path: Path) -> None:
    """Verify the PR body sent to gh has all six canonical sections in order (E2E-024)."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    runner = FakeRunner()
    phase = PrCreationPhase(workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, state=_state(verdict="APPROVE"))

    await phase.run(ctx)

    pr_call = next(c for c in runner.calls if c[:3] == ["gh", "pr", "create"])
    body_idx = pr_call.index("--body")
    body = pr_call[body_idx + 1]
    assert "## User Story" in body
    assert "## Acceptance Criteria" in body
    assert "## Approach" in body
    assert "## E2E Tests" in body
    assert "## Notable Decisions" in body
    assert "## Out of Scope" in body
    assert (
        body.index("## User Story")
        < body.index("## Acceptance Criteria")
        < body.index("## Approach")
        < body.index("## E2E Tests")
        < body.index("## Notable Decisions")
        < body.index("## Out of Scope")
    )
