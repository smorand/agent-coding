"""Tests for the E2E writing phase (FR-007)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, LlmError, TokenUsage
from phases.base import OutcomeKind, PhaseContext
from phases.e2e_writing import (
    COMMIT_MESSAGE_TEMPLATE,
    E2E_WRITING_REPORT_FILENAME,
    E2eWritingPhase,
    parse_e2e_response,
    validate_test_path,
)
from state import PhaseName, PhaseRecord, State
from tools.base import SubprocessOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


class FakeLlmClient:
    """LlmClient test double with a canned response."""

    def __init__(self, response: ChatResponse | LlmError) -> None:
        self._response = response
        self.calls: list[Sequence[ChatMessage]] = []

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        del max_tokens, temperature
        self.calls.append(list(messages))
        if isinstance(self._response, LlmError):
            raise self._response
        return self._response

    async def aclose(self) -> None:
        pass


class FakeRunner:
    """SubprocessRunner test double that records argv and returns canned outcomes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.head_sha: str = "deadbeef" * 5
        self.fail_on: str | None = None  # set to e.g. "commit" to force a failure

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
        if self.fail_on and self.fail_on in argv_list:
            return SubprocessOutcome(returncode=1, stdout="", stderr=f"{self.fail_on} failed")
        if argv_list[:2] == ["git", "rev-parse"]:
            return SubprocessOutcome(returncode=0, stdout=self.head_sha + "\n", stderr="")
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


def _ok_response(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(input_tokens=200, output_tokens=300),
        model="e2e-model",
        finish_reason=FinishReason.STOP,
        duration_ms=30.0,
    )


def _state(ticket_id: str = "demo") -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id=ticket_id,
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.E2E_WRITING,
        phases=[PhaseRecord(name=PhaseName.E2E_WRITING)],
    )


def _make_workspace(tmp_path: Path, *, ticket_id: str = "demo") -> tuple[Path, Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "tests").mkdir()
    work_dir = workspace / ".agent_work" / ticket_id
    work_dir.mkdir(parents=True)
    ticket = workspace / "ticket.md"
    ticket.write_text(
        "---\nid: demo\ntitle: Add subtract\n---\n\n"
        "## Description\n\nImplement subtract.\n\n"
        "## Acceptance Criteria\n\n- AC-1: subtract works.\n",
        encoding="utf-8",
    )
    (work_dir / "plan.md").write_text("Touch src/calc.py.", encoding="utf-8")
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path, *, ticket_id: str = "demo") -> PhaseContext:
    return PhaseContext(state=_state(ticket_id=ticket_id), work_dir=work_dir, ticket_path=str(ticket_path))


_RESPONSE_OK_SINGLE_FILE = (
    "## FILE: tests/test_subtract.py\n"
    "```python\n"
    "# AC-1\n"
    "def test_subtract():\n"
    "    from calc import subtract\n"
    "    assert subtract(3, 1) == 2\n"
    "```\n"
)

_RESPONSE_OK_TWO_FILES = (
    "## FILE: tests/test_subtract.py\n"
    "```python\n"
    "# AC-1\n"
    "def test_basic():\n"
    "    assert True\n"
    "```\n\n"
    "## FILE: tests/test_subtract_edge.py\n"
    "```python\n"
    "# AC-2\n"
    "def test_edge():\n"
    "    assert True\n"
    "```\n"
)


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_e2e_response_extracts_single_file() -> None:
    """A response with one block yields one E2eFile with the inner content."""
    files = parse_e2e_response(_RESPONSE_OK_SINGLE_FILE)
    assert len(files) == 1
    assert files[0].path == "tests/test_subtract.py"
    assert "def test_subtract" in files[0].content


def test_parse_e2e_response_extracts_multiple_files() -> None:
    """A response with multiple blocks yields all of them in order."""
    files = parse_e2e_response(_RESPONSE_OK_TWO_FILES)
    assert [f.path for f in files] == [
        "tests/test_subtract.py",
        "tests/test_subtract_edge.py",
    ]


def test_parse_e2e_response_rejects_no_file_blocks() -> None:
    """A response without any FILE block raises ValueError."""
    with pytest.raises(ValueError, match="no '## FILE:'"):
        parse_e2e_response("just prose, no blocks")


def test_parse_e2e_response_rejects_duplicate_paths() -> None:
    """Two blocks targeting the same path are rejected."""
    dup = "## FILE: tests/test_x.py\n```python\nx = 1\n```\n\n## FILE: tests/test_x.py\n```python\ny = 2\n```\n"
    with pytest.raises(ValueError, match="duplicate file path"):
        parse_e2e_response(dup)


def test_parse_e2e_response_rejects_path_outside_tests() -> None:
    """A block targeting src/ is rejected."""
    bad = "## FILE: src/foo.py\n```python\nx=1\n```\n"
    with pytest.raises(ValueError, match="must start with 'tests/'"):
        parse_e2e_response(bad)


def test_parse_e2e_response_rejects_non_test_filename() -> None:
    """A block under tests/ but not matching test_*.py is rejected."""
    bad = "## FILE: tests/helper.py\n```python\nx=1\n```\n"
    with pytest.raises(ValueError, match="test_\\*\\.py"):
        parse_e2e_response(bad)


def test_validate_test_path_accepts_subdirectory() -> None:
    """Subdirectories under tests/ are allowed if the leaf is test_*.py."""
    validate_test_path("tests/feature/test_thing.py")


def test_validate_test_path_rejects_path_traversal() -> None:
    """Paths containing `..` are rejected."""
    with pytest.raises(ValueError, match="escapes the workspace"):
        validate_test_path("tests/../etc/passwd")


def test_validate_test_path_rejects_absolute_path() -> None:
    """Absolute paths are rejected."""
    with pytest.raises(ValueError, match="escapes the workspace"):
        validate_test_path("/tmp/test_x.py")


def test_validate_test_path_rejects_empty() -> None:
    """An empty path is rejected."""
    with pytest.raises(ValueError, match="empty file path"):
        validate_test_path("")


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_without_llm_client_is_noop_continue(tmp_path: Path) -> None:
    """No LLM client: phase logs and returns CONTINUE without writing tests."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    phase = E2eWritingPhase()

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE


async def test_run_writes_files_and_commits_them(tmp_path: Path) -> None:
    """A successful run writes the LLM-produced tests and runs git add+commit."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(_ok_response(_RESPONSE_OK_SINGLE_FILE))
    runner = FakeRunner()
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    written = workspace / "tests" / "test_subtract.py"
    assert written.exists()
    assert "def test_subtract" in written.read_text(encoding="utf-8")

    # git add and git commit were called with the right shape.
    add_calls = [c for c in runner.calls if c[:2] == ["git", "add"]]
    commit_calls = [c for c in runner.calls if c[:2] == ["git", "commit"]]
    revparse_calls = [c for c in runner.calls if c[:2] == ["git", "rev-parse"]]
    assert len(add_calls) == 1
    assert "tests/test_subtract.py" in add_calls[0]
    assert len(commit_calls) == 1
    expected_message = COMMIT_MESSAGE_TEMPLATE.format(ticket_id="demo")
    assert expected_message in " ".join(commit_calls[0])
    assert len(revparse_calls) == 1


async def test_run_persists_e2e_writing_json_with_metadata(tmp_path: Path) -> None:
    """The persisted JSON records commit_sha, model, token usage, and files."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(_ok_response(_RESPONSE_OK_TWO_FILES))
    runner = FakeRunner()
    runner.head_sha = "abc123abc123abc123abc123abc123abc123abc1"
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)

    await phase.run(_ctx(work_dir, ticket))

    payload = json.loads((work_dir / E2E_WRITING_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["commit_sha"] == runner.head_sha
    assert payload["model"] == "e2e-model"
    assert payload["input_tokens"] == 200
    assert payload["output_tokens"] == 300
    paths = {f["path"] for f in payload["files"]}
    assert paths == {"tests/test_subtract.py", "tests/test_subtract_edge.py"}


async def test_run_records_commit_sha_on_state(tmp_path: Path) -> None:
    """The phase mutates ctx.state.e2e_commit_sha so the impl phase can verify it."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(_ok_response(_RESPONSE_OK_SINGLE_FILE))
    runner = FakeRunner()
    runner.head_sha = "feedface" * 5
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket)

    await phase.run(ctx)

    assert ctx.state.e2e_commit_sha == runner.head_sha


async def test_run_includes_plan_in_user_prompt(tmp_path: Path) -> None:
    """The user prompt includes the contents of plan.md when present."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    (work_dir / "plan.md").write_text("Use subtract in src/calc.py.", encoding="utf-8")
    fake_llm = FakeLlmClient(_ok_response(_RESPONSE_OK_SINGLE_FILE))
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=FakeRunner())

    await phase.run(_ctx(work_dir, ticket))

    user_msg = fake_llm.calls[0][1].content
    assert "Plan" in user_msg
    assert "src/calc.py" in user_msg


async def test_run_returns_halt_error_on_llm_failure(tmp_path: Path) -> None:
    """An LlmError becomes HALT_ERROR; no files written, no git command run."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(LlmError("endpoint unreachable"))
    runner = FakeRunner()
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "endpoint unreachable" in outcome.message
    assert runner.calls == []
    assert not (workspace / "tests" / "test_subtract.py").exists()


async def test_run_returns_halt_error_on_malformed_response(tmp_path: Path) -> None:
    """A response with no FILE block returns HALT_ERROR; no commit attempted."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(_ok_response("just prose"))
    runner = FakeRunner()
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "malformed" in outcome.message
    assert runner.calls == []


async def test_run_returns_halt_error_when_git_commit_fails(tmp_path: Path) -> None:
    """A failing git commit step yields HALT_ERROR with the underlying error."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake_llm = FakeLlmClient(_ok_response(_RESPONSE_OK_SINGLE_FILE))
    runner = FakeRunner()
    runner.fail_on = "commit"
    phase = E2eWritingPhase(llm_client=fake_llm, workspace=workspace, git_runner=runner)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "commit" in outcome.message
