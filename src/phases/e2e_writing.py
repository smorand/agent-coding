"""End-to-end test writing phase (FR-007).

Reads the ticket's acceptance criteria and the planning artifacts, asks
the configured LLM for one or more pytest test files, writes them under
`tests/`, and commits them with the message `Add E2E tests for <ticket-id>`.

The committed test files become the gate for the implementation phase:
the existing `AntiCheatGuard` blocks any future write to `tests/test_*.py`
while the IMPLEMENTATION phase is active. This phase itself is allowed
to write under `tests/` (the guard does not block E2E_WRITING).

Per the spec the phase has no carryover context from comprehension or
planning beyond the ticket and the plan: we read `plan.md` directly off
disk and re-prompt the model with a fresh system message.

When no LLM client is configured the phase logs and returns CONTINUE,
preserving the skeleton behavior used by tests that don't need a model.
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
from tools.git_ops import GitAddTool, GitCommitTool
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from llm.base import LlmClient
    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

E2E_WRITING_REPORT_FILENAME = "e2e_writing.json"
PLAN_FILENAME = "plan.md"

COMMIT_MESSAGE_TEMPLATE = "Add E2E tests for {ticket_id}"

SYSTEM_PROMPT = (
    "You are the end-to-end test writing phase of an autonomous coding "
    "agent. Read the ticket's acceptance criteria and the plan, then "
    "produce one or more pytest test files under tests/ that cover every "
    "acceptance criterion.\n\n"
    "Hard constraints:\n"
    "- Every file path MUST start with 'tests/' and the leaf filename "
    "MUST match 'test_*.py'. Subdirectories under tests/ are allowed.\n"
    "- Each test function MUST be preceded by a comment of the form "
    "'# AC-<N>' mapping it to one of the acceptance criteria. A single "
    "test may map to several ACs (e.g. '# AC-1, AC-3').\n"
    "- Tests MUST be runnable: pytest must be able to collect them. They "
    "are EXPECTED to fail at this stage; do not stub or import modules "
    "that do not exist yet, but DO write the assertions as if the feature "
    "were already implemented.\n"
    "- Do not write any source file outside of tests/.\n\n"
    "Output format - your response MUST consist exclusively of one or "
    "more file blocks in the following exact format:\n\n"
    "## FILE: tests/test_<name>.py\n"
    "```python\n"
    "<file content>\n"
    "```\n\n"
    "Produce no other text outside file blocks. No prose, no commentary."
)

_FILE_BLOCK_PATTERN = re.compile(
    r"^##\s*FILE:\s*(?P<path>\S+)\s*\n```(?:python|py)?\s*\n(?P<content>.*?)(?<=\n)```",
    re.DOTALL | re.MULTILINE,
)


@dataclass(frozen=True)
class E2eFile:
    """One test file produced by the LLM."""

    path: str
    content: str


@dataclass(frozen=True)
class E2eWritingReport:
    """Persisted artifact recording the E2E writing call."""

    files: tuple[E2eFile, ...]
    commit_sha: str
    model: str
    input_tokens: int
    output_tokens: int
    generated_at: datetime


@dataclass(frozen=True)
class _CommitResult:
    ok: bool
    sha: str
    error: str


class E2eWritingPhase(Phase):
    """Write the E2E tests in isolation, then commit them (FR-007)."""

    name = PhaseName.E2E_WRITING

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
        """Generate, persist, and commit the E2E test files."""
        if self._llm_client is None:
            logger.info("e2e_writing: no LLM client configured, skipping")
            return PhaseOutcome()
        workspace = self._resolve_workspace(ctx)
        ticket_path = Path(ctx.ticket_path)
        try:
            user_prompt = await asyncio.to_thread(
                self._build_user_prompt,
                ticket_path,
                ctx.work_dir,
            )
        except OSError as exc:
            logger.exception("e2e_writing: failed to read inputs")
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"e2e_writing failed to read inputs: {exc}",
            )
        try:
            response = await self._llm_client.complete(
                [
                    ChatMessage(role=Role.SYSTEM, content=SYSTEM_PROMPT),
                    ChatMessage(role=Role.USER, content=user_prompt),
                ]
            )
        except LlmError as exc:
            logger.warning("e2e_writing LLM call failed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"e2e_writing LLM call failed: {exc}",
            )
        try:
            files = parse_e2e_response(response.content)
        except ValueError as exc:
            logger.warning("e2e_writing response could not be parsed: %s", exc)
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"e2e_writing response malformed: {exc}",
            )
        await asyncio.to_thread(self._materialize_files, workspace, files)
        commit_result = await self._commit_tests(workspace, files, ctx.state.ticket_id)
        if not commit_result.ok:
            return PhaseOutcome(
                kind=OutcomeKind.HALT_ERROR,
                message=f"e2e_writing commit failed: {commit_result.error}",
            )
        report = E2eWritingReport(
            files=files,
            commit_sha=commit_result.sha,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            generated_at=datetime.now(UTC),
        )
        await asyncio.to_thread(self._persist_report, ctx.work_dir, report)
        # Record the commit SHA on the run state so the implementation phase
        # can verify the lock has not been tampered with (E2E-026).
        ctx.state.e2e_commit_sha = commit_result.sha
        logger.info(
            "e2e_writing committed %d file(s) (sha=%s, model=%s)",
            len(report.files),
            report.commit_sha,
            report.model,
        )
        return PhaseOutcome()

    def _resolve_workspace(self, ctx: PhaseContext) -> Path:
        if self._workspace is not None:
            return self._workspace
        return ctx.work_dir.parent.parent

    @staticmethod
    def _build_user_prompt(ticket_path: Path, work_dir: Path) -> str:
        ticket_text = ticket_path.read_text(encoding="utf-8") if ticket_path.exists() else ""
        plan_path = work_dir / PLAN_FILENAME
        plan_text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        sections = [f"### Ticket: {ticket_path.name}\n\n{ticket_text}"]
        if plan_text:
            sections.append(f"### Plan ({PLAN_FILENAME})\n\n{plan_text}")
        return "\n\n".join(sections)

    @staticmethod
    def _materialize_files(workspace: Path, files: tuple[E2eFile, ...]) -> None:
        for f in files:
            target = workspace / f.path
            target.parent.mkdir(parents=True, exist_ok=True)
            content = f.content if f.content.endswith("\n") else f.content + "\n"
            target.write_text(content, encoding="utf-8")

    async def _commit_tests(
        self,
        workspace: Path,
        files: tuple[E2eFile, ...],
        ticket_id: str,
    ) -> _CommitResult:
        runner: SubprocessRunner = self._git_runner or AsyncSubprocessRunner()
        adder = GitAddTool(workspace, runner=runner)
        committer = GitCommitTool(workspace, runner=runner)
        add_result = await adder.call(paths=[f.path for f in files])
        if not add_result.ok:
            return _CommitResult(ok=False, sha="", error=f"git add: {add_result.error}")
        message = COMMIT_MESSAGE_TEMPLATE.format(ticket_id=ticket_id)
        commit_result = await committer.call(message=message)
        if not commit_result.ok:
            return _CommitResult(ok=False, sha="", error=f"git commit: {commit_result.error}")
        outcome = await runner.run(["git", "rev-parse", "HEAD"], cwd=workspace)
        sha = outcome.stdout.strip() if outcome.returncode == 0 else ""
        return _CommitResult(ok=True, sha=sha, error="")

    @staticmethod
    def _persist_report(work_dir: Path, report: E2eWritingReport) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / E2E_WRITING_REPORT_FILENAME
        payload = {
            "commit_sha": report.commit_sha,
            "model": report.model,
            "input_tokens": report.input_tokens,
            "output_tokens": report.output_tokens,
            "generated_at": report.generated_at.isoformat(),
            "files": [{"path": f.path, "size_bytes": len(f.content.encode("utf-8"))} for f in report.files],
        }
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_e2e_response(text: str) -> tuple[E2eFile, ...]:
    """Extract `(path, content)` pairs from the LLM response.

    Each block must look like:

        ## FILE: tests/test_<name>.py
        ```python
        ...
        ```

    Validates that every path is under `tests/` and matches `test_*.py`.
    Raises `ValueError` if no blocks are found, paths are invalid, or
    duplicate paths appear in the response.
    """
    matches = list(_FILE_BLOCK_PATTERN.finditer(text))
    if not matches:
        msg = "no '## FILE:' blocks found in response"
        raise ValueError(msg)
    files: list[E2eFile] = []
    seen: set[str] = set()
    for match in matches:
        raw_path = match.group("path").strip()
        content = match.group("content")
        validate_test_path(raw_path)
        if raw_path in seen:
            msg = f"duplicate file path in response: {raw_path}"
            raise ValueError(msg)
        seen.add(raw_path)
        files.append(E2eFile(path=raw_path, content=content))
    return tuple(files)


def validate_test_path(path: str) -> None:
    """Reject paths that escape `tests/`, contain `..`, or violate `test_*.py`."""
    if not path:
        msg = "empty file path"
        raise ValueError(msg)
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or any(part == ".." for part in pure.parts):
        msg = f"path escapes the workspace: {path}"
        raise ValueError(msg)
    if not pure.parts or pure.parts[0] != "tests":
        msg = f"path must start with 'tests/': {path}"
        raise ValueError(msg)
    leaf = pure.name
    if not (leaf.startswith("test_") and leaf.endswith(".py")):
        msg = f"test filename must match 'test_*.py': {path}"
        raise ValueError(msg)
