"""Implementation loop phase (FR-008, FR-009, FR-010).

Two nested loops:

- The OUTER loop (`_ApproachRunner`) explores up to `min_approaches`
  distinct attempts. Each attempt that fails resets the working tree to
  the recorded E2E commit (`git reset --hard <e2e_commit_sha>`), logs
  the attempt to `.agent_work/<ticket-id>/approaches/<n>.md`, and starts
  fresh with the failure log of prior approaches added to the prompt.

- The INNER loop (`_IterationRunner`) drives one approach: ask the LLM
  for `## FILE:` edits, apply them, run `make check`, feed the output
  back. Stop conditions: `make check` exit 0 (CONVERGED), max iterations
  (MAX_ITER), same failing-test signature for `stagnation_threshold`
  rounds (STAGNATION), regression vs the best seen state (REGRESSION),
  or the global wall-clock budget exhausted (WALL_CLOCK).

Context compression (FR-010): every `summarize_every` iterations, the
older history is squashed into `.agent_work/<ticket-id>/loop_summary.md`
via the `summarizer` LLM client (when one is provided); subsequent
prompts include the summary instead of the older iteration tails.

Anti-cheat: writes to `tests/test_*.py` are rejected at parse time
(matching the AntiCheatGuard rule from `tools.anti_cheat`).

When no LLM client is configured the phase logs and returns CONTINUE
(skeleton fallback).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from llm.base import ChatMessage, LlmError, Role
from phases.base import OutcomeKind, Phase, PhaseContext, PhaseOutcome
from state import PhaseName
from tools.anti_cheat import is_test_locked_path
from tools.git_ops import GitAddTool, GitCommitTool, GitDiffTool
from tools.make_runner import MakeTool
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from llm.base import LlmClient
    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

IMPLEMENTATION_REPORT_FILENAME = "implementation.json"
APPROACHES_DIRNAME = "approaches"
LOOP_SUMMARY_FILENAME = "loop_summary.md"
PLAN_FILENAME = "plan.md"

DEFAULT_MAX_ITERATIONS = 30  # FR-008 default
DEFAULT_STAGNATION_THRESHOLD = 5  # FR-008 default
DEFAULT_MIN_APPROACHES = 3  # FR-009 default
DEFAULT_WALL_CLOCK_SECONDS = 7200  # FR-008 default (2h)
DEFAULT_SUMMARIZE_EVERY = 10  # FR-010 default K
DEFAULT_KEEP_RECENT = 5  # last N iterations kept verbatim in prompt
DEFAULT_DIFF_CAP_BYTES = 32_000
DEFAULT_OUTPUT_CAP_BYTES = 16_000

COMMIT_MESSAGE_TEMPLATE = "Implement {ticket_id}"

_FILE_BLOCK_PATTERN = re.compile(
    r"^##\s*FILE:\s*(?P<path>\S+)\s*\n```(?:python|py)?\s*\n(?P<content>.*?)(?<=\n)```",
    re.DOTALL | re.MULTILINE,
)
_FAILING_TEST_PATTERN = re.compile(r"^FAILED\s+([^\s]+::[^\s]+)", re.MULTILINE)

SYSTEM_PROMPT_BASE = (
    "You are the implementation phase of an autonomous coding agent. "
    "The end-to-end tests under tests/ have already been written and "
    "committed; they currently fail. Your job is to write or edit "
    "source files (typically under src/) so that `make check` exits 0 "
    "with every E2E test passing.\n\n"
    "Hard constraints:\n"
    "- You MUST NOT modify any file under tests/ that matches "
    "'tests/test_*.py'. Those files are locked. Edits to "
    "tests/conftest.py and tests/testdata/** are allowed.\n"
    "- You MAY create new files anywhere outside the locked tests.\n"
    "- Your response MUST consist exclusively of '## FILE:' blocks of "
    "the form:\n\n"
    "## FILE: <relative path>\n"
    "```python\n"
    "<full file content>\n"
    "```\n\n"
    "After your reply, the agent will write each file and run `make "
    "check`. Its output is fed back to you. Iterate until make check "
    "exits 0."
)

SUMMARIZER_SYSTEM_PROMPT = (
    "You are the context-compression model for an autonomous coding "
    "agent's implementation loop. Compress the iteration history below "
    "into a tight markdown summary under 600 words. Preserve: the files "
    "that have been edited, the latest failing-test set, recurring "
    "errors, and any insight that would help the next iteration. Do NOT "
    "include line-by-line stack traces or repeated boilerplate."
)


class _IterationStop(StrEnum):
    """Reason an iteration loop stopped."""

    CONVERGED = "converged"
    MAX_ITER = "max_iterations"
    STAGNATION = "stagnation"
    REGRESSION = "regression"
    WALL_CLOCK = "wall_clock"
    LLM_ERROR = "llm_error"


@dataclass(frozen=True)
class EditedFile:
    """One file produced by an iteration of the loop."""

    path: str
    content: str


@dataclass(frozen=True)
class IterationOutcome:
    """Outcome of one iteration in the implementation loop."""

    iteration: int
    approach: int
    files_edited: tuple[str, ...]
    check_returncode: int
    check_stdout: str
    check_stderr: str
    failing_signature: str
    failing_count: int
    parse_error: str = ""


@dataclass(frozen=True)
class ApproachAttempt:
    """One full approach attempt in the multi-approach loop."""

    number: int
    iterations: tuple[IterationOutcome, ...]
    stop_reason: str
    stop_message: str


@dataclass(frozen=True)
class ImplementationReport:
    """Persisted artifact recording every approach attempt."""

    approaches: tuple[ApproachAttempt, ...]
    final_status: str  # 'converged' | 'exhausted'
    commit_sha: str
    model: str
    summarizer_model: str
    total_input_tokens: int
    total_output_tokens: int
    wall_clock_seconds: float
    generated_at: datetime


@dataclass
class _LoopState:
    """Mutable state shared between approaches: tokens, history summary."""

    total_in: int = 0
    total_out: int = 0
    last_model: str = ""
    summarizer_model: str = ""
    summary: str = ""  # rolling compressed summary of older iterations
    summarized_up_to: int = 0  # global iteration index covered by `summary`
    approach_failure_log: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _IterationStopOutcome:
    """Returned by `_IterationRunner.run` to signal why it stopped."""

    reason: _IterationStop
    iterations: tuple[IterationOutcome, ...]
    message: str


class ImplementationPhase(Phase):
    """Implement the code that makes the E2E tests pass (FR-008/009/010)."""

    name = PhaseName.IMPLEMENTATION

    __slots__ = (
        "_keep_recent",
        "_llm_client",
        "_max_iterations",
        "_min_approaches",
        "_runner",
        "_stagnation_threshold",
        "_summarize_every",
        "_summarizer_client",
        "_wall_clock_seconds",
        "_workspace",
    )

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        summarizer_client: LlmClient | None = None,
        workspace: Path | None = None,
        runner: SubprocessRunner | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        stagnation_threshold: int = DEFAULT_STAGNATION_THRESHOLD,
        min_approaches: int = DEFAULT_MIN_APPROACHES,
        wall_clock_seconds: int = DEFAULT_WALL_CLOCK_SECONDS,
        summarize_every: int = DEFAULT_SUMMARIZE_EVERY,
        keep_recent: int = DEFAULT_KEEP_RECENT,
    ) -> None:
        self._llm_client = llm_client
        self._summarizer_client = summarizer_client
        self._workspace = workspace
        self._runner = runner
        self._max_iterations = max_iterations
        self._stagnation_threshold = stagnation_threshold
        self._min_approaches = min_approaches
        self._wall_clock_seconds = wall_clock_seconds
        self._summarize_every = summarize_every
        self._keep_recent = keep_recent

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Drive the multi-approach loop until convergence or exhaustion."""
        if self._llm_client is None:
            logger.info("implementation: no LLM client configured, skipping")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        runner: SubprocessRunner = self._runner or AsyncSubprocessRunner()
        ticket_path = Path(ctx.ticket_path)
        try:
            ticket_text, plan_text = await asyncio.to_thread(_read_inputs, ticket_path, ctx.work_dir)
        except OSError as exc:
            logger.exception("implementation: failed to read inputs")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"implementation failed to read inputs: {exc}",
            )
        approach_runner = _ApproachRunner(
            llm_client=self._llm_client,
            summarizer_client=self._summarizer_client,
            workspace=workspace,
            runner=runner,
            ticket_text=ticket_text,
            plan_text=plan_text,
            ticket_id=ctx.state.ticket_id,
            work_dir=ctx.work_dir,
            e2e_commit_sha=ctx.state.e2e_commit_sha,
            review_concerns=ctx.state.review_concerns or "",
            max_iterations=self._max_iterations,
            stagnation_threshold=self._stagnation_threshold,
            min_approaches=self._min_approaches,
            wall_clock_seconds=self._wall_clock_seconds,
            summarize_every=self._summarize_every,
            keep_recent=self._keep_recent,
        )
        report, message = await approach_runner.run()
        await asyncio.to_thread(_persist_report, ctx.work_dir, report)
        if report.final_status == "converged":
            ctx.state.implementation_commit_sha = report.commit_sha
            logger.info(
                "implementation converged on approach %d after %d iteration(s); commit=%s",
                len(report.approaches),
                sum(len(a.iterations) for a in report.approaches),
                report.commit_sha,
            )
            return PhaseOutcome()
        logger.warning(
            "implementation exhausted after %d approach(es) / %d iteration(s)",
            len(report.approaches),
            sum(len(a.iterations) for a in report.approaches),
        )
        return PhaseOutcome(
            kind=OutcomeKind.HALT_EXHAUSTED,
            message=message or "implementation loop exhausted",
        )

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        return ctx.work_dir.parent.parent


class _ApproachRunner:
    """Outer loop: explores up to `min_approaches` attempts (FR-009)."""

    __slots__ = (
        "_e2e_commit_sha",
        "_keep_recent",
        "_llm_client",
        "_max_iterations",
        "_min_approaches",
        "_plan_text",
        "_review_concerns",
        "_runner",
        "_stagnation_threshold",
        "_started_at",
        "_state",
        "_summarize_every",
        "_summarizer_client",
        "_ticket_id",
        "_ticket_text",
        "_wall_clock_seconds",
        "_work_dir",
        "_workspace",
    )

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        summarizer_client: LlmClient | None,
        workspace: Path,
        runner: SubprocessRunner,
        ticket_text: str,
        plan_text: str,
        ticket_id: str,
        work_dir: Path,
        e2e_commit_sha: str | None,
        review_concerns: str,
        max_iterations: int,
        stagnation_threshold: int,
        min_approaches: int,
        wall_clock_seconds: int,
        summarize_every: int,
        keep_recent: int,
    ) -> None:
        self._llm_client = llm_client
        self._summarizer_client = summarizer_client
        self._workspace = workspace
        self._runner = runner
        self._ticket_text = ticket_text
        self._plan_text = plan_text
        self._ticket_id = ticket_id
        self._work_dir = work_dir
        self._e2e_commit_sha = e2e_commit_sha
        self._review_concerns = review_concerns
        self._max_iterations = max_iterations
        self._stagnation_threshold = stagnation_threshold
        self._min_approaches = min_approaches
        self._wall_clock_seconds = wall_clock_seconds
        self._summarize_every = summarize_every
        self._keep_recent = keep_recent
        self._state = _LoopState()
        self._started_at = 0.0

    async def run(self) -> tuple[ImplementationReport, str]:
        """Run up to `min_approaches` attempts; return the final report and message."""
        self._started_at = time.monotonic()
        approaches: list[ApproachAttempt] = []
        commit_sha = ""
        for n in range(1, self._min_approaches + 1):
            inner = _IterationRunner(
                approach_number=n,
                llm_client=self._llm_client,
                summarizer_client=self._summarizer_client,
                workspace=self._workspace,
                runner=self._runner,
                ticket_text=self._ticket_text,
                plan_text=self._plan_text,
                ticket_id=self._ticket_id,
                work_dir=self._work_dir,
                review_concerns=self._review_concerns,
                shared=self._state,
                max_iterations=self._max_iterations,
                stagnation_threshold=self._stagnation_threshold,
                summarize_every=self._summarize_every,
                keep_recent=self._keep_recent,
                started_at=self._started_at,
                wall_clock_seconds=self._wall_clock_seconds,
            )
            inner_outcome = await inner.run()
            attempt = ApproachAttempt(
                number=n,
                iterations=inner_outcome.iterations,
                stop_reason=inner_outcome.reason.value,
                stop_message=inner_outcome.message,
            )
            approaches.append(attempt)
            await asyncio.to_thread(self._persist_approach_log, attempt)
            if inner_outcome.reason == _IterationStop.CONVERGED:
                commit_sha = await _commit_implementation(
                    self._workspace,
                    self._runner,
                    self._ticket_id,
                    inner_outcome.iterations[-1].files_edited,
                )
                return self._build_report("converged", commit_sha, approaches), ""
            if inner_outcome.reason == _IterationStop.WALL_CLOCK:
                return self._build_report("exhausted", "", approaches), inner_outcome.message
            # Reset the working tree to the E2E commit before the next attempt.
            await self._reset_to_e2e()
            self._state.approach_failure_log.append(
                f"Approach {n} failed: {inner_outcome.reason.value} - {inner_outcome.message}"
            )
        last_message = approaches[-1].stop_message if approaches else ""
        outer_message = (
            f"exhausted after {len(approaches)} approach(es); last reason: {last_message}"
            if last_message
            else f"all {self._min_approaches} approach(es) exhausted"
        )
        return self._build_report("exhausted", "", approaches), outer_message

    async def _reset_to_e2e(self) -> None:
        if not self._e2e_commit_sha:
            logger.warning("approach reset skipped: no e2e_commit_sha recorded")
            return
        outcome = await self._runner.run(
            ["git", "reset", "--hard", self._e2e_commit_sha],
            cwd=self._workspace,
        )
        if outcome.returncode != 0:
            logger.warning("approach reset failed: %s", outcome.stderr)

    def _persist_approach_log(self, attempt: ApproachAttempt) -> None:
        approaches_dir = self._work_dir / APPROACHES_DIRNAME
        approaches_dir.mkdir(parents=True, exist_ok=True)
        target = approaches_dir / f"{attempt.number:02d}.md"
        last_signature = attempt.iterations[-1].failing_signature if attempt.iterations else ""
        lines = [
            f"# Approach {attempt.number}",
            "",
            f"**Stop reason**: {attempt.stop_reason}",
            "",
            f"**Stop message**: {attempt.stop_message}",
            "",
            f"**Iterations**: {len(attempt.iterations)}",
            "",
            f"**Last failing signature**: {last_signature or '<none>'}",
            "",
            "## Iteration tails",
            "",
        ]
        for it in attempt.iterations[-3:]:
            lines.append(f"### Iteration {it.iteration}")
            lines.append(f"files: {', '.join(it.files_edited) or '<none>'}")
            lines.append(f"rc: {it.check_returncode}")
            lines.append(f"failing: {it.failing_signature or '<none>'}")
            lines.append("")
        target.write_text("\n".join(lines), encoding="utf-8")

    def _build_report(
        self,
        status: str,
        commit_sha: str,
        approaches: list[ApproachAttempt],
    ) -> ImplementationReport:
        wall = time.monotonic() - self._started_at if self._started_at else 0.0
        return ImplementationReport(
            approaches=tuple(approaches),
            final_status=status,
            commit_sha=commit_sha,
            model=self._state.last_model,
            summarizer_model=self._state.summarizer_model,
            total_input_tokens=self._state.total_in,
            total_output_tokens=self._state.total_out,
            wall_clock_seconds=wall,
            generated_at=datetime.now(UTC),
        )


class _IterationRunner:
    """Inner loop: drives one approach worth of iterations."""

    __slots__ = (
        "_approach_number",
        "_iterations",
        "_keep_recent",
        "_llm_client",
        "_max_iterations",
        "_plan_text",
        "_review_concerns",
        "_runner",
        "_shared",
        "_stagnation_threshold",
        "_started_at",
        "_summarize_every",
        "_summarizer_client",
        "_ticket_id",
        "_ticket_text",
        "_wall_clock_seconds",
        "_work_dir",
        "_workspace",
    )

    def __init__(
        self,
        *,
        approach_number: int,
        llm_client: LlmClient,
        summarizer_client: LlmClient | None,
        workspace: Path,
        runner: SubprocessRunner,
        ticket_text: str,
        plan_text: str,
        ticket_id: str,
        work_dir: Path,
        review_concerns: str,
        shared: _LoopState,
        max_iterations: int,
        stagnation_threshold: int,
        summarize_every: int,
        keep_recent: int,
        started_at: float,
        wall_clock_seconds: int,
    ) -> None:
        self._approach_number = approach_number
        self._llm_client = llm_client
        self._summarizer_client = summarizer_client
        self._workspace = workspace
        self._runner = runner
        self._ticket_text = ticket_text
        self._plan_text = plan_text
        self._ticket_id = ticket_id
        self._work_dir = work_dir
        self._review_concerns = review_concerns
        self._shared = shared
        self._max_iterations = max_iterations
        self._stagnation_threshold = stagnation_threshold
        self._summarize_every = summarize_every
        self._keep_recent = keep_recent
        self._started_at = started_at
        self._wall_clock_seconds = wall_clock_seconds
        self._iterations: list[IterationOutcome] = []

    async def run(self) -> _IterationStopOutcome:
        """Iterate until convergence or one of the stop conditions fires."""
        repeat_signature = ""
        repeat_count = 0
        best_failing_count: int | None = None
        for i in range(1, self._max_iterations + 1):
            if self._wall_clock_exceeded():
                msg = f"wall-clock budget {self._wall_clock_seconds}s exceeded"
                return _IterationStopOutcome(
                    reason=_IterationStop.WALL_CLOCK,
                    iterations=tuple(self._iterations),
                    message=msg,
                )
            history_text = await self._build_history_text()
            current_diff = await _read_diff(self._workspace, self._runner)
            user_prompt = _build_user_prompt(
                ticket_text=self._ticket_text,
                plan_text=self._plan_text,
                diff_text=current_diff,
                history_text=history_text,
                approach_number=self._approach_number,
                approach_failure_log=self._shared.approach_failure_log,
                review_concerns=self._review_concerns,
            )
            try:
                response = await self._llm_client.complete(
                    [
                        ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT_BASE),
                        ChatMessage(role=Role.USER, content=user_prompt),
                    ]
                )
            except LlmError as exc:
                self._record_iteration(
                    iteration=i,
                    files_edited=(),
                    rc=-1,
                    stdout="",
                    stderr="",
                    signature="<llm-error>",
                    failing_count=-1,
                    parse_error=str(exc),
                )
                return _IterationStopOutcome(
                    reason=_IterationStop.LLM_ERROR,
                    iterations=tuple(self._iterations),
                    message=f"LLM call failed in iteration {i}: {exc}",
                )
            self._shared.total_in += response.usage.input_tokens
            self._shared.total_out += response.usage.output_tokens
            self._shared.last_model = response.model
            try:
                files = parse_implementation_response(response.content)
            except ValueError as exc:
                self._record_iteration(
                    iteration=i,
                    files_edited=(),
                    rc=-1,
                    stdout="",
                    stderr="",
                    signature="<parse-error>",
                    failing_count=-1,
                    parse_error=str(exc),
                )
                continue
            await asyncio.to_thread(_apply_edits, self._workspace, files)
            check = await _run_make_check(self._runner, self._workspace)
            signature = _failing_signature(check.stdout + "\n" + check.stderr)
            failing_count = signature.count(",") + 1 if signature else 0
            self._record_iteration(
                iteration=i,
                files_edited=tuple(f.path for f in files),
                rc=check.returncode,
                stdout=_truncate(check.stdout, DEFAULT_OUTPUT_CAP_BYTES),
                stderr=_truncate(check.stderr, DEFAULT_OUTPUT_CAP_BYTES),
                signature=signature,
                failing_count=failing_count,
            )
            if check.returncode == 0:
                return _IterationStopOutcome(
                    reason=_IterationStop.CONVERGED,
                    iterations=tuple(self._iterations),
                    message="",
                )
            # Regression detection: more failures than the best seen so far.
            if best_failing_count is not None and failing_count > best_failing_count:
                msg = f"regression: {failing_count} failing > best {best_failing_count}; forcing approach change"
                return _IterationStopOutcome(
                    reason=_IterationStop.REGRESSION,
                    iterations=tuple(self._iterations),
                    message=msg,
                )
            if best_failing_count is None or failing_count < best_failing_count:
                best_failing_count = failing_count
            # Stagnation detection.
            if signature == repeat_signature:
                repeat_count += 1
            else:
                repeat_signature = signature
                repeat_count = 1
            if repeat_count >= self._stagnation_threshold:
                msg = f"loop stagnated: same failing tests for {repeat_count} iterations ({signature[:80]})"
                return _IterationStopOutcome(
                    reason=_IterationStop.STAGNATION,
                    iterations=tuple(self._iterations),
                    message=msg,
                )
            # Periodic summarization (FR-010).
            await self._maybe_summarize()
        return _IterationStopOutcome(
            reason=_IterationStop.MAX_ITER,
            iterations=tuple(self._iterations),
            message=f"max iterations {self._max_iterations} reached",
        )

    def _wall_clock_exceeded(self) -> bool:
        return time.monotonic() - self._started_at > self._wall_clock_seconds

    def _record_iteration(
        self,
        *,
        iteration: int,
        files_edited: tuple[str, ...],
        rc: int,
        stdout: str,
        stderr: str,
        signature: str,
        failing_count: int,
        parse_error: str = "",
    ) -> None:
        self._iterations.append(
            IterationOutcome(
                iteration=iteration,
                approach=self._approach_number,
                files_edited=files_edited,
                check_returncode=rc,
                check_stdout=stdout,
                check_stderr=stderr,
                failing_signature=signature,
                failing_count=failing_count,
                parse_error=parse_error,
            )
        )

    async def _build_history_text(self) -> str:
        recent = self._iterations[-self._keep_recent :] if self._iterations else []
        sections: list[str] = []
        if self._shared.summary:
            sections.append(
                f"### Compressed history (iterations 1..{self._shared.summarized_up_to})\n\n{self._shared.summary}"
            )
        if recent:
            sections.append("### Recent iterations\n\n" + _format_iterations(recent))
        elif not self._shared.summary:
            return "No iterations yet."
        return "\n\n".join(sections)

    async def _maybe_summarize(self) -> None:
        if self._summarizer_client is None:
            return
        total = len(self._iterations)
        if total < self._summarize_every:
            return
        # Summarize whenever we cross a multiple of summarize_every.
        if total - self._shared.summarized_up_to < self._summarize_every:
            return
        # Collect the iterations from `summarized_up_to + 1` up to `total - keep_recent`.
        cutoff = max(0, total - self._keep_recent)
        if cutoff <= self._shared.summarized_up_to:
            return
        to_compress = self._iterations[self._shared.summarized_up_to : cutoff]
        body = _format_iterations(to_compress)
        try:
            response = await self._summarizer_client.complete(
                [
                    ChatMessage(role=Role.SYSTEM, content=SUMMARIZER_SYSTEM_PROMPT),
                    ChatMessage(
                        role=Role.USER,
                        content=(
                            f"Existing summary (may be empty):\n\n"
                            f"{self._shared.summary}\n\n"
                            f"New iterations to fold in:\n\n{body}"
                        ),
                    ),
                ]
            )
        except LlmError as exc:
            logger.warning("summarizer call failed; continuing without compression: %s", exc)
            return
        self._shared.total_in += response.usage.input_tokens
        self._shared.total_out += response.usage.output_tokens
        self._shared.summarizer_model = response.model
        self._shared.summary = response.content.strip()
        self._shared.summarized_up_to = cutoff
        await asyncio.to_thread(
            _persist_loop_summary,
            self._work_dir,
            self._shared.summary,
            self._shared.summarized_up_to,
        )


def parse_implementation_response(text: str) -> tuple[EditedFile, ...]:
    """Extract edits from the LLM response and reject locked test files.

    Validates each path: must be non-empty, no traversal, not absolute,
    and NOT match `is_test_locked_path` (i.e. not `tests/test_*.py`).
    Raises `ValueError` if no blocks are found, any path is invalid, or
    the response duplicates a path.
    """
    matches = list(_FILE_BLOCK_PATTERN.finditer(text))
    if not matches:
        msg = "no '## FILE:' blocks found in response"
        raise ValueError(msg)
    files: list[EditedFile] = []
    seen: set[str] = set()
    for match in matches:
        raw_path = match.group("path").strip()
        content = match.group("content")
        validate_implementation_path(raw_path)
        if raw_path in seen:
            msg = f"duplicate file path in response: {raw_path}"
            raise ValueError(msg)
        seen.add(raw_path)
        files.append(EditedFile(path=raw_path, content=content))
    return tuple(files)


def validate_implementation_path(path: str) -> None:
    """Reject empty, absolute, traversing, or locked-test paths."""
    if not path:
        msg = "empty file path"
        raise ValueError(msg)
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        msg = f"path escapes the workspace: {path}"
        raise ValueError(msg)
    if is_test_locked_path(path):
        msg = f"path is locked for the implementation phase: {path}"
        raise ValueError(msg)


def _read_inputs(ticket_path: Path, work_dir: Path) -> tuple[str, str]:
    ticket_text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
    plan_path = work_dir / PLAN_FILENAME
    plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    return ticket_text, plan_text


async def _read_diff(workspace: Path, runner: SubprocessRunner) -> str:
    diff_tool = GitDiffTool(workspace, runner=runner)
    result = await diff_tool.call()
    if not result.ok:
        return ""
    return _truncate(result.output, DEFAULT_DIFF_CAP_BYTES)


@dataclass(frozen=True)
class _CheckOutcome:
    returncode: int
    stdout: str
    stderr: str


async def _run_make_check(runner: SubprocessRunner, workspace: Path) -> _CheckOutcome:
    make_tool = MakeTool(workspace, runner=runner)
    result = await make_tool.call(target="check")
    rc_meta = result.metadata.get("returncode") if result.metadata else None
    rc = rc_meta if isinstance(rc_meta, int) else (0 if result.ok else 1)
    return _CheckOutcome(returncode=rc, stdout=result.output, stderr=result.error)


def _failing_signature(combined_output: str) -> str:
    """Extract sorted, deduped pytest failure ids from combined stdout/stderr."""
    matches = sorted(set(_FAILING_TEST_PATTERN.findall(combined_output)))
    return ",".join(matches)


def _apply_edits(workspace: Path, files: tuple[EditedFile, ...]) -> None:
    for f in files:
        target = workspace / f.path
        target.parent.mkdir(parents=True, exist_ok=True)
        content = f.content if f.content.endswith("\n") else f.content + "\n"
        target.write_text(content, encoding="utf-8")


async def _commit_implementation(
    workspace: Path,
    runner: SubprocessRunner,
    ticket_id: str,
    files: tuple[str, ...],
) -> str:
    """Stage every edited path and commit with the canonical message; return HEAD SHA."""
    if not files:
        return ""
    adder = GitAddTool(workspace, runner=runner)
    committer = GitCommitTool(workspace, runner=runner)
    add_result = await adder.call(paths=list(files))
    if not add_result.ok:
        return ""
    message = COMMIT_MESSAGE_TEMPLATE.format(ticket_id=ticket_id)
    commit_result = await committer.call(message=message)
    if not commit_result.ok:
        return ""
    rev = await runner.run(["git", "rev-parse", "HEAD"], cwd=workspace)
    return rev.stdout.strip() if rev.returncode == 0 else ""


def _persist_report(work_dir: Path, report: ImplementationReport) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / IMPLEMENTATION_REPORT_FILENAME
    payload = {
        "final_status": report.final_status,
        "commit_sha": report.commit_sha,
        "model": report.model,
        "summarizer_model": report.summarizer_model,
        "total_input_tokens": report.total_input_tokens,
        "total_output_tokens": report.total_output_tokens,
        "wall_clock_seconds": round(report.wall_clock_seconds, 3),
        "generated_at": report.generated_at.isoformat(),
        "approaches": [
            {
                "number": a.number,
                "stop_reason": a.stop_reason,
                "stop_message": a.stop_message,
                "iterations": [_iteration_to_dict(it) for it in a.iterations],
            }
            for a in report.approaches
        ],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _iteration_to_dict(it: IterationOutcome) -> dict[str, object]:
    return {
        "iteration": it.iteration,
        "approach": it.approach,
        "files_edited": list(it.files_edited),
        "check_returncode": it.check_returncode,
        "check_stdout": it.check_stdout,
        "check_stderr": it.check_stderr,
        "failing_signature": it.failing_signature,
        "failing_count": it.failing_count,
        "parse_error": it.parse_error,
    }


def _persist_loop_summary(work_dir: Path, summary: str, up_to: int) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / LOOP_SUMMARY_FILENAME
    body = f"<!-- iterations 1..{up_to} -->\n\n{summary}\n"
    target.write_text(body, encoding="utf-8")


def _format_iterations(iterations: list[IterationOutcome]) -> str:
    if not iterations:
        return "<none>"
    parts: list[str] = []
    for it in iterations:
        block = (
            f"#### Iteration {it.iteration} (approach {it.approach})\n"
            f"files: {', '.join(it.files_edited) or '<none>'}\n"
            f"make check returncode: {it.check_returncode}\n"
            f"failing tests ({it.failing_count}): {it.failing_signature or '<none>'}\n"
            + (f"parse_error: {it.parse_error}\n" if it.parse_error else "")
            + f"stdout (tail):\n{_truncate(it.check_stdout, 1_500)}\n"
            + f"stderr (tail):\n{_truncate(it.check_stderr, 1_500)}\n"
        )
        parts.append(block)
    return "\n".join(parts)


def _build_user_prompt(
    *,
    ticket_text: str,
    plan_text: str,
    diff_text: str,
    history_text: str,
    approach_number: int,
    approach_failure_log: list[str],
    review_concerns: str,
) -> str:
    sections = [
        f"### Approach {approach_number}",
        f"### Ticket\n\n{ticket_text}",
    ]
    if plan_text:
        sections.append(f"### Plan\n\n{plan_text}")
    if approach_failure_log:
        sections.append(
            "### Previous approaches (failed)\n\n"
            + "\n".join(f"- {entry}" for entry in approach_failure_log)
            + "\n\nDo NOT repeat the same approach. Try a meaningfully different "
            "module structure, algorithm, library, or data model."
        )
    if review_concerns:
        sections.append(
            f"### Reviewer blocking concerns (re-run)\n\n{review_concerns}\n\n"
            "Address each concern explicitly in your edits."
        )
    if diff_text:
        sections.append(f"### Current diff\n\n```diff\n{diff_text}\n```")
    sections.append(f"### Iteration history\n\n{history_text}")
    return "\n\n".join(sections)


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n... [truncated]"


__all__ = [
    "APPROACHES_DIRNAME",
    "DEFAULT_KEEP_RECENT",
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_MIN_APPROACHES",
    "DEFAULT_STAGNATION_THRESHOLD",
    "DEFAULT_SUMMARIZE_EVERY",
    "DEFAULT_WALL_CLOCK_SECONDS",
    "IMPLEMENTATION_REPORT_FILENAME",
    "LOOP_SUMMARY_FILENAME",
    "ApproachAttempt",
    "EditedFile",
    "ImplementationPhase",
    "ImplementationReport",
    "IterationOutcome",
    "parse_implementation_response",
    "validate_implementation_path",
]
