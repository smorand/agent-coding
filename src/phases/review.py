"""Reviewer phase (FR-011).

Runs with a fresh LLM context after the implementation loop converges.
Inputs: ticket, plan, full branch diff (vs the E2E commit), list of
files modified. Asks the reviewer model for a structured verdict
(`APPROVE` or `REQUEST_CHANGES`) with concerns split into `blocking`
and `suggestion` severities. Persists `review.json` for the PR-creation
step to consume.

Per the spec the phase always checks:
  (a) tests still validate the acceptance criteria
  (b) CLAUDE.md / .agent_docs/ updated when modules or conventions change
  (c) README.md updated for user-visible behavior changes
  (d) PR template fields can be populated

For the MVP we always return CONTINUE on a successful review; the
verdict is consumed via `review.json` and `state.review_verdict`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from llm.base import ChatMessage, LlmError, Role
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName
from tools.git_ops import GitDiffTool, GitStatusTool
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from llm.base import LlmClient
    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

REVIEW_REPORT_FILENAME = "review.json"
PLAN_FILENAME = "plan.md"

DIFF_MAX_BYTES = 60_000

SECTION_VERDICT = "## VERDICT"
SECTION_BLOCKING = "## BLOCKING"
SECTION_SUGGESTIONS = "## SUGGESTIONS"
SECTION_SUMMARY = "## SUMMARY"

SYSTEM_PROMPT = (
    "You are the reviewer phase of an autonomous coding agent. Read the "
    "ticket, the plan, the full branch diff, and the changed files; then "
    "produce a structured verdict.\n\n"
    "You MUST check, in this order:\n"
    "  (a) the E2E tests added in tests/test_*.py still validate every "
    "acceptance criterion in the ticket;\n"
    "  (b) CLAUDE.md and any relevant .agent_docs/ are updated to reflect "
    "new modules or conventions introduced by the diff;\n"
    "  (c) README.md is updated when user-visible behavior changes;\n"
    "  (d) the canonical PR template fields can be populated.\n\n"
    "Output format - your response MUST contain the four sections below "
    "in this exact order with these exact headers:\n\n"
    "## VERDICT\n\n"
    "Either 'APPROVE' or 'REQUEST_CHANGES' on its own line.\n\n"
    "## BLOCKING\n\n"
    "One bullet per blocking concern, formatted '- <path>:<line> - <reason>' "
    "where <line> is an integer or '?' when unknown. Write 'None.' if no "
    "blocking concerns.\n\n"
    "## SUGGESTIONS\n\n"
    "Same bullet format for non-blocking suggestions, or 'None.'.\n\n"
    "## SUMMARY\n\n"
    "Two or three lines summarizing the review."
)


class ReviewVerdict(StrEnum):
    """Outcome of the reviewer phase."""

    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"


@dataclass(frozen=True)
class ReviewConcern:
    """One concern raised by the reviewer."""

    path: str
    line: str  # integer-as-string, or '?'
    severity: str  # 'blocking' or 'suggestion'
    reason: str


@dataclass(frozen=True)
class ReviewReport:
    """Persisted artifact recording the review call."""

    verdict: ReviewVerdict
    blocking: tuple[ReviewConcern, ...]
    suggestions: tuple[ReviewConcern, ...]
    summary: str
    model: str
    input_tokens: int
    output_tokens: int
    generated_at: datetime


class ReviewPhase(Phase):
    """Review the implementation result with a fresh-context agent (FR-011)."""

    name = PhaseName.REVIEW

    __slots__ = ("_git_runner", "_llm_client", "_workspace")

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        workspace: Path | None = None,
        git_runner: SubprocessRunner | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._workspace = workspace
        self._git_runner = git_runner

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Collect inputs, call the LLM, persist the verdict."""
        if self._llm_client is None:
            logger.info("review: no LLM client configured, skipping")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        ticket_path = Path(ctx.ticket_path)
        try:
            inputs_text = await self._collect_inputs(workspace, ticket_path, ctx)
        except OSError as exc:
            logger.exception("review: failed to collect inputs")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"review failed to collect inputs: {exc}",
            )
        try:
            response = await self._llm_client.complete(
                [
                    ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
                    ChatMessage(role=Role.USER, content=inputs_text),
                ]
            )
        except LlmError as exc:
            logger.warning("review LLM call failed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"review LLM call failed: {exc}",
            )
        try:
            verdict, blocking, suggestions, summary = parse_review_response(response.content)
        except ValueError as exc:
            logger.warning("review response could not be parsed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"review response malformed: {exc}",
            )
        # Doc-maintenance gate (FR-016): if the diff touches src/ but neither
        # CLAUDE.md, README.md, nor any .agent_docs/ file, append an
        # auto-blocking concern. This is the deterministic complement to
        # the reviewer's prompt-side check.
        diff_text, _ = await self._collect_git_inputs(workspace, ctx.state.e2e_commit_sha)
        doc_concern = _detect_missing_doc_update(diff_text)
        if doc_concern is not None:
            blocking = (*blocking, doc_concern)
            verdict = ReviewVerdict.REQUEST_CHANGES
            logger.info("review: doc-maintenance gate auto-flagged %s", doc_concern.path)
        report = ReviewReport(
            verdict=verdict,
            blocking=blocking,
            suggestions=suggestions,
            summary=summary,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            generated_at=datetime.now(UTC),
        )
        await asyncio.to_thread(self._persist_report, ctx.work_dir, report)
        ctx.state.review_verdict = verdict.value
        if verdict == ReviewVerdict.APPROVE:
            # Clear blocking concerns from a previous re-run; they are stale now.
            ctx.state.review_concerns = None
        logger.info(
            "review verdict: %s (blocking=%d, suggestions=%d)",
            verdict.value,
            len(blocking),
            len(suggestions),
        )
        return PhaseOutcome()

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        return ctx.work_dir.parent.parent

    async def _collect_inputs(
        self,
        workspace: Path,
        ticket_path: Path,
        ctx: PhaseContext,
    ) -> str:
        ticket_text = await asyncio.to_thread(_read_text_or_empty, ticket_path)
        plan_text = await asyncio.to_thread(_read_text_or_empty, ctx.work_dir / PLAN_FILENAME)
        diff_text, files_text = await self._collect_git_inputs(workspace, ctx.state.e2e_commit_sha)
        sections = [f"### Ticket: {ticket_path.name}\n\n{ticket_text}"]
        if plan_text:
            sections.append(f"### Plan ({PLAN_FILENAME})\n\n{plan_text}")
        if files_text:
            sections.append(f"### Files modified\n\n{files_text}")
        if diff_text:
            sections.append(f"### Diff\n\n```diff\n{diff_text}\n```")
        return "\n\n".join(sections)

    async def _collect_git_inputs(
        self,
        workspace: Path,
        e2e_commit_sha: str | None,
    ) -> tuple[str, str]:
        runner: SubprocessRunner = self._git_runner or AsyncSubprocessRunner()
        diff_tool = GitDiffTool(workspace, runner=runner)
        if e2e_commit_sha:
            diff_result = await diff_tool.call(path=e2e_commit_sha)
        else:
            diff_result = await diff_tool.call()
        diff_text = _truncate_diff(diff_result.output if diff_result.ok else "")
        status_tool = GitStatusTool(workspace, runner=runner)
        status_result = await status_tool.call()
        files_text = status_result.output if status_result.ok else ""
        return diff_text, files_text

    @staticmethod
    def _persist_report(work_dir: Path, report: ReviewReport) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / REVIEW_REPORT_FILENAME
        payload = {
            "verdict": report.verdict.value,
            "summary": report.summary,
            "blocking": [_concern_to_dict(c) for c in report.blocking],
            "suggestions": [_concern_to_dict(c) for c in report.suggestions],
            "model": report.model,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "generated_at": report.generated_at.isoformat(),
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _concern_to_dict(c: ReviewConcern) -> dict[str, str]:
    return {"path": c.path, "line": c.line, "severity": c.severity, "reason": c.reason}


def _read_text_or_empty(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _truncate_diff(text: str) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= DIFF_MAX_BYTES:
        return text
    return encoded[:DIFF_MAX_BYTES].decode("utf-8", errors="ignore") + "\n... [diff truncated]"


_BULLET_PATTERN = re.compile(r"^[-*]\s+(.+)$", re.MULTILINE)
_CONCERN_PATTERN = re.compile(r"^(?P<path>[^\s:]+):(?P<line>\d+|\?)\s*[-—]\s*(?P<reason>.+)$")
_DIFF_PATH_PATTERN = re.compile(r"^\+\+\+ b/(?P<path>\S+)", re.MULTILINE)


def _detect_missing_doc_update(diff_text: str) -> ReviewConcern | None:
    """Return a doc-maintenance concern when src/ changed without docs (FR-016).

    Heuristic: if any `+++ b/<path>` header in the diff lives under `src/`
    (i.e. production code changed) but neither CLAUDE.md, README.md, nor
    any path under `.agent_docs/` was touched, return a synthetic blocking
    concern. Pure: easy to test, low false-positive risk for the common
    case (most src/ changes warrant at least a CLAUDE.md mention).
    """
    paths = _DIFF_PATH_PATTERN.findall(diff_text)
    if not paths:
        return None
    touched_src = any(p.startswith("src/") for p in paths)
    touched_docs = any(p in {"CLAUDE.md", "README.md"} or p.startswith(".agent_docs/") for p in paths)
    if touched_src and not touched_docs:
        return ReviewConcern(
            path="CLAUDE.md",
            line="?",
            severity="blocking",
            reason=(
                "diff touches src/ but neither CLAUDE.md, README.md, nor .agent_docs/ "
                "were updated; FR-016 requires doc maintenance for new modules / "
                "user-visible behavior changes"
            ),
        )
    return None


def parse_review_response(
    text: str,
) -> tuple[ReviewVerdict, tuple[ReviewConcern, ...], tuple[ReviewConcern, ...], str]:
    """Parse the LLM response; raise ValueError if it does not follow the contract."""
    sections = _split_sections(text)
    verdict_lines = sections.get("verdict", "").strip().splitlines()
    if not verdict_lines:
        msg = "VERDICT section is empty"
        raise ValueError(msg)
    raw = verdict_lines[0].strip().upper()
    try:
        verdict = ReviewVerdict(raw)
    except ValueError as exc:
        msg = f"unknown verdict {raw!r}; expected APPROVE or REQUEST_CHANGES"
        raise ValueError(msg) from exc
    blocking = _parse_concerns(sections.get("blocking", ""), severity="blocking")
    suggestions = _parse_concerns(sections.get("suggestions", ""), severity="suggestion")
    summary = sections.get("summary", "").strip()
    if not summary:
        msg = "SUMMARY section is empty"
        raise ValueError(msg)
    return verdict, blocking, suggestions, summary


def _split_sections(text: str) -> dict[str, str]:
    spans = {
        "verdict": SECTION_VERDICT,
        "blocking": SECTION_BLOCKING,
        "suggestions": SECTION_SUGGESTIONS,
        "summary": SECTION_SUMMARY,
    }
    indexes: dict[str, int] = {}
    for key, header in spans.items():
        idx = text.find(header)
        if idx < 0:
            msg = f"missing required section: {header}"
            raise ValueError(msg)
        indexes[key] = idx
    ordered = sorted(indexes.items(), key=lambda kv: kv[1])
    bodies: dict[str, str] = {}
    for i, (key, start) in enumerate(ordered):
        end = ordered[i + 1][1] if i + 1 < len(ordered) else len(text)
        bodies[key] = text[start + len(spans[key]) : end].strip()
    return bodies


def _parse_concerns(body: str, *, severity: str) -> tuple[ReviewConcern, ...]:
    stripped = body.strip()
    if not stripped or stripped.lower().startswith("none"):
        return ()
    items: list[ReviewConcern] = []
    for line in _BULLET_PATTERN.findall(stripped):
        match = _CONCERN_PATTERN.match(line.strip())
        if match is None:
            items.append(ReviewConcern(path="", line="?", severity=severity, reason=line.strip()))
            continue
        items.append(
            ReviewConcern(
                path=match.group("path"),
                line=match.group("line"),
                severity=severity,
                reason=match.group("reason").strip(),
            )
        )
    return tuple(items)
