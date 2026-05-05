"""Implementation loop phase (FR-008 / FR-009 / FR-010).

Iterative loop that asks the configured LLM for source-file edits,
applies them, runs `make check`, feeds the output back into the next
iteration, and stops when the build is green (exit 0) or when one of
the bail-out conditions fires (max iterations or stagnation).

For the MVP we run a single approach. Multi-approach exploration
(FR-009) and rolling-summary context compression (FR-010) are deferred
to follow-ups; this phase keeps a tail of the most recent iterations in
the prompt up to a byte cap.

Anti-cheat: file writes to `tests/test_*.py` are rejected at apply
time. Writes to `tests/conftest.py` and `tests/testdata/**` remain
allowed, matching the spec.

When no LLM client is configured the phase logs and returns CONTINUE
(skeleton fallback).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
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
PLAN_FILENAME = "plan.md"

DEFAULT_MAX_ITERATIONS = 10  # MVP: trimmed from spec default 30 for cost control
DEFAULT_STAGNATION_THRESHOLD = 5
DEFAULT_DIFF_CAP_BYTES = 32_000
DEFAULT_OUTPUT_CAP_BYTES = 16_000

COMMIT_MESSAGE_TEMPLATE = "Implement {ticket_id}"

SYSTEM_PROMPT = (
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
    "check`. Its output (returncode, stdout/stderr) is fed back to you. "
    "Iterate until make check exits 0."
)

_FILE_BLOCK_PATTERN = re.compile(
    r"^##\s*FILE:\s*(?P<path>\S+)\s*\n```(?:python|py)?\s*\n(?P<content>.*?)(?<=\n)```",
    re.DOTALL | re.MULTILINE,
)
_FAILING_TEST_PATTERN = re.compile(r"^FAILED\s+([^\s]+::[^\s]+)", re.MULTILINE)


@dataclass(frozen=True)
class EditedFile:
    """One file produced by an iteration of the loop."""

    path: str
    content: str


@dataclass(frozen=True)
class IterationOutcome:
    """Outcome of one iteration in the implementation loop."""

    iteration: int
    files_edited: tuple[str, ...]
    check_returncode: int
    check_stdout: str
    check_stderr: str
    failing_signature: str
    parse_error: str = ""


@dataclass(frozen=True)
class ImplementationReport:
    """Persisted artifact recording the loop run."""

    iterations: tuple[IterationOutcome, ...]
    final_status: str  # 'converged' | 'exhausted'
    commit_sha: str
    model: str
    total_input_tokens: int
    total_output_tokens: int
    generated_at: datetime


@dataclass(frozen=True)
class _LoopOutcome:
    report: ImplementationReport
    message: str


class ImplementationPhase(Phase):
    """Implement the code that makes the E2E tests pass (FR-008, FR-009, FR-010)."""

    name = PhaseName.IMPLEMENTATION

    __slots__ = (
        "_llm_client",
        "_max_iterations",
        "_runner",
        "_stagnation_threshold",
        "_workspace",
    )

    def __init__(
        self,
        *,
        llm_client: LlmClient | None = None,
        workspace: Path | None = None,
        runner: SubprocessRunner | None = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        stagnation_threshold: int = DEFAULT_STAGNATION_THRESHOLD,
    ) -> None:
        self._llm_client = llm_client
        self._workspace = workspace
        self._runner = runner
        self._max_iterations = max_iterations
        self._stagnation_threshold = stagnation_threshold

    async def run(self, ctx: PhaseContext) -> PhaseOutcome:
        """Drive the loop until make check passes or iterations are exhausted."""
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
        loop = _LoopRunner(
            llm_client=self._llm_client,
            workspace=workspace,
            runner=runner,
            ticket_text=ticket_text,
            plan_text=plan_text,
            ticket_id=ctx.state.ticket_id,
            max_iterations=self._max_iterations,
            stagnation_threshold=self._stagnation_threshold,
        )
        outcome = await loop.run()
        await asyncio.to_thread(_persist_report, ctx.work_dir, outcome.report)
        if outcome.report.final_status == "converged":
            ctx.state.implementation_commit_sha = outcome.report.commit_sha
            logger.info(
                "implementation converged after %d iteration(s); commit=%s",
                len(outcome.report.iterations),
                outcome.report.commit_sha,
            )
            return PhaseOutcome()
        logger.warning(
            "implementation exhausted after %d iteration(s)",
            len(outcome.report.iterations),
        )
        return PhaseOutcome(
            kind=OutcomeKind.HALT_EXHAUSTED,
            message=outcome.message or "implementation loop exhausted",
        )

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        return ctx.work_dir.parent.parent


class _LoopRunner:
    """Drives one approach worth of iterations. Stateful, single-use."""

    __slots__ = (
        "_iterations",
        "_last_model",
        "_llm_client",
        "_max_iterations",
        "_plan_text",
        "_runner",
        "_stagnation_threshold",
        "_ticket_id",
        "_ticket_text",
        "_total_in",
        "_total_out",
        "_workspace",
    )

    def __init__(
        self,
        *,
        llm_client: LlmClient,
        workspace: Path,
        runner: SubprocessRunner,
        ticket_text: str,
        plan_text: str,
        ticket_id: str,
        max_iterations: int,
        stagnation_threshold: int,
    ) -> None:
        self._llm_client = llm_client
        self._workspace = workspace
        self._runner = runner
        self._ticket_text = ticket_text
        self._plan_text = plan_text
        self._ticket_id = ticket_id
        self._max_iterations = max_iterations
        self._stagnation_threshold = stagnation_threshold
        self._iterations: list[IterationOutcome] = []
        self._total_in = 0
        self._total_out = 0
        self._last_model = ""

    async def run(self) -> _LoopOutcome:
        """Iterate until convergence or exhaustion."""
        repeat_signature = ""
        repeat_count = 0
        for i in range(1, self._max_iterations + 1):
            history = _format_history(self._iterations)
            current_diff = await _read_diff(self._workspace, self._runner)
            user_prompt = _build_user_prompt(
                ticket_text=self._ticket_text,
                plan_text=self._plan_text,
                diff_text=current_diff,
                history_text=history,
            )
            try:
                response = await self._llm_client.complete(
                    [
                        ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
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
                    parse_error=str(exc),
                )
                return self._exhausted(f"LLM call failed in iteration {i}: {exc}")
            self._total_in += response.usage.input_tokens
            self._total_out += response.usage.output_tokens
            self._last_model = response.model
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
                    parse_error=str(exc),
                )
                continue
            await asyncio.to_thread(_apply_edits, self._workspace, files)
            check = await _run_make_check(self._runner, self._workspace)
            signature = _failing_signature(check.stdout + "\n" + check.stderr)
            self._record_iteration(
                iteration=i,
                files_edited=tuple(f.path for f in files),
                rc=check.returncode,
                stdout=_truncate(check.stdout, DEFAULT_OUTPUT_CAP_BYTES),
                stderr=_truncate(check.stderr, DEFAULT_OUTPUT_CAP_BYTES),
                signature=signature,
            )
            if check.returncode == 0:
                commit_sha = await _commit_implementation(self._workspace, self._runner, self._ticket_id, files)
                return _LoopOutcome(report=self._build_report("converged", commit_sha), message="")
            if signature == repeat_signature:
                repeat_count += 1
            else:
                repeat_signature = signature
                repeat_count = 1
            if repeat_count >= self._stagnation_threshold:
                msg = f"loop stagnated: same failing tests for {repeat_count} iterations ({signature[:80]})"
                return self._exhausted(msg)
        return self._exhausted("max iterations reached without convergence")

    def _record_iteration(
        self,
        *,
        iteration: int,
        files_edited: tuple[str, ...],
        rc: int,
        stdout: str,
        stderr: str,
        signature: str,
        parse_error: str = "",
    ) -> None:
        self._iterations.append(
            IterationOutcome(
                iteration=iteration,
                files_edited=files_edited,
                check_returncode=rc,
                check_stdout=stdout,
                check_stderr=stderr,
                failing_signature=signature,
                parse_error=parse_error,
            )
        )

    def _exhausted(self, message: str) -> _LoopOutcome:
        return _LoopOutcome(report=self._build_report("exhausted", ""), message=message)

    def _build_report(self, status: str, commit_sha: str) -> ImplementationReport:
        return ImplementationReport(
            iterations=tuple(self._iterations),
            final_status=status,
            commit_sha=commit_sha,
            model=self._last_model,
            total_input_tokens=self._total_in,
            total_output_tokens=self._total_out,
            generated_at=datetime.now(UTC),
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
    files: tuple[EditedFile, ...],
) -> str:
    """Stage every edited path and commit with the canonical message; return HEAD SHA."""
    adder = GitAddTool(workspace, runner=runner)
    committer = GitCommitTool(workspace, runner=runner)
    add_result = await adder.call(paths=[f.path for f in files])
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
        "total_input_tokens": report.total_input_tokens,
        "total_output_tokens": report.total_output_tokens,
        "generated_at": report.generated_at.isoformat(),
        "iterations": [
            {
                "iteration": it.iteration,
                "files_edited": list(it.files_edited),
                "check_returncode": it.check_returncode,
                "check_stdout": it.check_stdout,
                "check_stderr": it.check_stderr,
                "failing_signature": it.failing_signature,
                "parse_error": it.parse_error,
            }
            for it in report.iterations
        ],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _format_history(iterations: list[IterationOutcome]) -> str:
    if not iterations:
        return "No iterations yet."
    parts: list[str] = []
    for it in iterations[-5:]:
        block = (
            f"### Iteration {it.iteration}\n"
            f"files: {', '.join(it.files_edited) or '<none>'}\n"
            f"make check returncode: {it.check_returncode}\n"
            f"failing tests: {it.failing_signature or '<none>'}\n"
            + (f"parse_error: {it.parse_error}\n" if it.parse_error else "")
            + f"stdout (tail):\n{_truncate(it.check_stdout, 2_000)}\n"
            + f"stderr (tail):\n{_truncate(it.check_stderr, 2_000)}\n"
        )
        parts.append(block)
    return "\n".join(parts)


def _build_user_prompt(
    *,
    ticket_text: str,
    plan_text: str,
    diff_text: str,
    history_text: str,
) -> str:
    sections = [f"### Ticket\n\n{ticket_text}"]
    if plan_text:
        sections.append(f"### Plan\n\n{plan_text}")
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
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_STAGNATION_THRESHOLD",
    "IMPLEMENTATION_REPORT_FILENAME",
    "EditedFile",
    "ImplementationPhase",
    "ImplementationReport",
    "IterationOutcome",
    "parse_implementation_response",
    "validate_implementation_path",
]
