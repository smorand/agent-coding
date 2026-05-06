"""Pull Request creation phase (FR-012).

Final phase. Verifies the E2E commit lock is intact (E2E-026), pushes
the feature branch, builds the canonical PR body via `pr_template`,
opens the PR via `gh pr create`, and posts a comment back on the ticket
when the ticket reference looks like a GitHub issue. Persists
`pr_creation.json` and stores the resulting URL on `state.pr_url`.

When the run looks blocked (the implementation loop never converged or
the reviewer requested changes), the phase opens a draft PR with the
`agent-impl-blocked` label and unchecked ACs in the body, per the
canonical template's blocked mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from phases.pr_template import (
    BLOCKED_LABEL,
    AcceptanceCriterion,
    AttemptedApproach,
    PrTemplateInputs,
    PrTemplateOutputs,
    TestReference,
    build_pr_body,
)
from state import PhaseName
from tools.gh import (
    GhIssueCommentTool,
    GhLabelEnsureTool,
    GhPrCreateTool,
)
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

PR_CREATION_REPORT_FILENAME = "pr_creation.json"
PLAN_FILENAME = "plan.md"

DEFAULT_BLOCKED_DECOMPOSITION = (
    "Split the failing acceptance criteria into smaller follow-up tickets; "
    "each decomposed ticket should fit one approach attempt."
)
DEFAULT_NOTABLE_DECISIONS = "_See `plan.md` for the rationale and notable choices._"
DEFAULT_OUT_OF_SCOPE = "_None recorded; nothing was deliberately deferred._"
DEFAULT_FALLBACK_APPROACH = "_See `plan.md`._"
IMPLEMENTATION_REPORT_FILENAME = "implementation.json"

_AC_PATTERN = re.compile(r"^\s*-\s*(AC-\d+)\s*:\s*(.+?)\s*$", re.MULTILINE)
_TEST_DEF_PATTERN = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_AC_COMMENT_PATTERN = re.compile(r"#\s*(AC-\d+(?:\s*,\s*AC-\d+)*)")
_TICKET_TITLE_PATTERN = re.compile(r"^title:\s*(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PrCreationReport:
    """Persisted artifact recording the PR creation."""

    pr_url: str
    is_blocked: bool
    is_draft: bool
    labels: tuple[str, ...]
    title: str
    generated_at: datetime


class PrCreationPhase(Phase):
    """Open the Pull Request and notify the ticket (FR-012)."""

    name = PhaseName.PR_CREATION

    __slots__ = ("_git_runner", "_workspace")

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        git_runner: SubprocessRunner | None = None,
    ) -> None:
        self._workspace = workspace
        self._git_runner = git_runner

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:  # noqa: PLR0911 - many short error paths
        """Verify lock, push, build body, open PR, comment ticket."""
        # Skeleton fallback: when no E2E commit was recorded (e.g., the run had
        # no LLM-configured e2e_writing phase), there is nothing to publish.
        if not ctx.state.e2e_commit_sha:
            logger.info("pr_creation: no e2e_commit_sha recorded, skipping PR creation")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        runner: SubprocessRunner = self._git_runner or AsyncSubprocessRunner()
        ticket_path = Path(ctx.ticket_path)
        # E2E-026: refuse to open a PR if the recorded SHA is no longer in history.
        if not await _commit_exists(runner, workspace, ctx.state.e2e_commit_sha):
            logger.error(
                "pr_creation: recorded e2e_commit_sha %s not in branch history; aborting",
                ctx.state.e2e_commit_sha,
            )
            return PhaseOutcome(
                kind=OutcomeKind.HALT_EXHAUSTED,
                message="E2E commit was modified after lock; aborting",
            )
        try:
            inputs = await asyncio.to_thread(
                _build_template_inputs,
                workspace,
                ticket_path,
                ctx.work_dir,
                ctx.state.review_verdict,
            )
        except OSError as exc:
            logger.exception("pr_creation: failed to assemble template inputs")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"pr_creation failed to assemble inputs: {exc}",
            )
        try:
            outputs = build_pr_body(inputs, title=_derive_title(ticket_path))
        except ValueError as exc:
            logger.warning("pr_creation: PR body validation failed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"pr_creation: invalid PR body: {exc}",
            )
        # Ensure the blocked label exists before applying it.
        if outputs.is_blocked:
            label_tool = GhLabelEnsureTool(workspace, runner=runner)
            label_result = await label_tool.call(
                name=BLOCKED_LABEL,
                color="b60205",
                description="Implementation loop exhausted; PR opened in blocked state",
            )
            if not label_result.ok:
                logger.warning("pr_creation: could not ensure %r label: %s", BLOCKED_LABEL, label_result.error)
        push_result = await runner.run(["git", "push", "-u", "origin", "HEAD"], cwd=workspace)
        if push_result.returncode != 0:
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"pr_creation: git push failed: {push_result.stderr}",
            )
        pr_tool = GhPrCreateTool(workspace, runner=runner)
        pr_result = await pr_tool.call(
            title=outputs.title,
            body=outputs.body,
            draft=outputs.draft,
            labels=list(outputs.labels) if outputs.labels else None,
        )
        if not pr_result.ok:
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"pr_creation: gh pr create failed: {pr_result.error}",
            )
        pr_url = _extract_pr_url(pr_result.output) or pr_result.output.strip()
        ctx.state.pr_url = pr_url
        # If the ticket reference looks like a GitHub issue (#NNN or full URL),
        # post a comment with the PR link.
        await self._notify_ticket(workspace, runner, ticket_path, pr_url)
        report = PrCreationReport(
            pr_url=pr_url,
            is_blocked=outputs.is_blocked,
            is_draft=outputs.draft,
            labels=outputs.labels,
            title=outputs.title,
            generated_at=datetime.now(UTC),
        )
        await asyncio.to_thread(_persist_report, ctx.work_dir, report)
        logger.info("pr_creation: opened %s (blocked=%s)", pr_url, outputs.is_blocked)
        return PhaseOutcome()

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        return ctx.work_dir.parent.parent

    @staticmethod
    async def _notify_ticket(
        workspace: Path,
        runner: SubprocessRunner,
        ticket_path: Path,
        pr_url: str,
    ) -> None:
        issue_ref = _detect_github_issue_ref(ticket_path)
        if issue_ref is None:
            return
        comment_tool = GhIssueCommentTool(workspace, runner=runner)
        comment_body = f"agent-code opened a Pull Request: {pr_url}"
        result = await comment_tool.call(issue_number=issue_ref, body=comment_body)
        if not result.ok:
            logger.warning("pr_creation: could not comment on issue %s: %s", issue_ref, result.error)


def _build_template_inputs(
    workspace: Path,
    ticket_path: Path,
    work_dir: Path,
    review_verdict: str | None,
) -> PrTemplateInputs:
    ticket_text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
    plan_path = work_dir / PLAN_FILENAME
    plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    is_blocked = review_verdict != "APPROVE"
    summary = _derive_summary(ticket_text)
    acs = _parse_acceptance_criteria(ticket_text, all_passed=not is_blocked)
    if not acs:
        acs = (AcceptanceCriterion(label="AC-1", text="implement the requested behavior", passed=not is_blocked),)
    e2e_tests = _collect_e2e_tests(workspace)
    if not e2e_tests:
        e2e_tests = (TestReference(pytest_path="tests/test_<feature>.py::test_<name>", acs=("AC-1",)),)
    approach = plan_text.strip() if plan_text.strip() else DEFAULT_FALLBACK_APPROACH
    attempted: tuple[AttemptedApproach, ...] = ()
    decomposition = ""
    if is_blocked:
        attempted = _read_attempted_approaches(work_dir)
        if not attempted:
            attempted = (
                AttemptedApproach(
                    name="primary",
                    why_failed=("reviewer requested changes or implementation loop did not converge"),
                ),
            )
        decomposition = DEFAULT_BLOCKED_DECOMPOSITION
    return PrTemplateInputs(
        ticket_reference=str(ticket_path),
        summary=summary,
        acceptance_criteria=acs,
        approach=approach,
        e2e_tests=e2e_tests,
        notable_decisions=DEFAULT_NOTABLE_DECISIONS,
        out_of_scope=DEFAULT_OUT_OF_SCOPE,
        attempted_approaches=attempted,
        proposed_decomposition=decomposition,
    )


def _read_attempted_approaches(work_dir: Path) -> tuple[AttemptedApproach, ...]:
    """Read implementation.json and surface every approach the impl loop tried."""
    path = work_dir / IMPLEMENTATION_REPORT_FILENAME
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    approaches = payload.get("approaches") or []
    return tuple(
        AttemptedApproach(
            name=f"approach-{a.get('number', i + 1)}",
            why_failed=str(a.get("stop_message", a.get("stop_reason", "no detail"))),
        )
        for i, a in enumerate(approaches)
    )


def _derive_title(ticket_path: Path) -> str:
    """Build the PR title from the ticket frontmatter `title:` or the filename."""
    text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
    match = _TICKET_TITLE_PATTERN.search(text)
    if match:
        return match.group(1).strip().strip('"').strip("'")
    return ticket_path.stem.replace("-", " ").replace("_", " ").strip() or "agent-code change"


def _derive_summary(ticket_text: str) -> str:
    match = _TICKET_TITLE_PATTERN.search(ticket_text)
    if match:
        return match.group(1).strip().strip('"').strip("'")
    return "automated change"


def _parse_acceptance_criteria(ticket_text: str, *, all_passed: bool) -> tuple[AcceptanceCriterion, ...]:
    """Parse `## Acceptance Criteria` bullets like `- AC-1: text`."""
    items: list[AcceptanceCriterion] = []
    for match in _AC_PATTERN.finditer(ticket_text):
        items.append(
            AcceptanceCriterion(
                label=match.group(1),
                text=match.group(2),
                passed=all_passed,
            )
        )
    return tuple(items)


def _collect_e2e_tests(workspace: Path) -> tuple[TestReference, ...]:
    """Walk `tests/` and extract `test_*` functions with their `# AC-N` comments.

    A test gets ACs from the contiguous block of comment lines immediately
    above its `def` (no blank lines or non-comment lines in between).
    Tests without such a comment fall back to `AC-1`.
    """
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return ()
    refs: list[TestReference] = []
    for path in sorted(tests_dir.rglob("test_*.py")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        rel = path.relative_to(workspace).as_posix()
        for idx, line in enumerate(lines):
            fn_def_match = _TEST_DEF_PATTERN.match(line)
            if fn_def_match is None:
                continue
            fn_name = fn_def_match.group(1)
            ac_labels = _ac_labels_above(lines, idx)
            acs = ac_labels if ac_labels else ("AC-1",)
            refs.append(TestReference(pytest_path=f"{rel}::{fn_name}", acs=acs))
    return tuple(refs)


def _ac_labels_above(lines: list[str], def_idx: int) -> tuple[str, ...]:
    """Return AC labels from the contiguous comment block immediately above `def_idx`."""
    j = def_idx - 1
    while j >= 0 and lines[j].strip().startswith("#"):
        match = _AC_COMMENT_PATTERN.search(lines[j])
        if match:
            return tuple(ac.strip() for ac in match.group(1).split(","))
        j -= 1
    return ()


async def _commit_exists(runner: SubprocessRunner, workspace: Path, sha: str) -> bool:
    """Return True iff `sha` resolves to a commit object in the repo."""
    outcome = await runner.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=workspace)
    return outcome.returncode == 0


_PR_URL_PATTERN = re.compile(r"https?://[^\s]+/pull/\d+")


def _extract_pr_url(text: str) -> str | None:
    match = _PR_URL_PATTERN.search(text)
    return match.group(0) if match else None


_GITHUB_ISSUE_URL_PATTERN = re.compile(r"https?://[^\s]+/issues/(\d+)")


def _detect_github_issue_ref(ticket_path: Path) -> str | None:
    """Return an issue number if the ticket text or path looks like a GitHub issue."""
    if not ticket_path.exists():
        return None
    text = ticket_path.read_text(encoding="utf-8")
    url_match = _GITHUB_ISSUE_URL_PATTERN.search(text)
    if url_match:
        return url_match.group(1)
    return None


def _persist_report(work_dir: Path, report: PrCreationReport) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / PR_CREATION_REPORT_FILENAME
    payload = {
        "pr_url": report.pr_url,
        "is_blocked": report.is_blocked,
        "is_draft": report.is_draft,
        "labels": list(report.labels),
        "title": report.title,
        "generated_at": report.generated_at.isoformat(),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


__all__ = [
    "PR_CREATION_REPORT_FILENAME",
    "PrCreationPhase",
    "PrCreationReport",
]


# Reference exposed so `phases/__init__` can re-export the dataclass alias.
_ = PrTemplateOutputs
