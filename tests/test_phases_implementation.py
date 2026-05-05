"""Tests for the implementation loop phase (FR-008)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, LlmError, TokenUsage
from phases.base import OutcomeKind, PhaseContext
from phases.implementation import (
    DEFAULT_STAGNATION_THRESHOLD,
    IMPLEMENTATION_REPORT_FILENAME,
    ImplementationPhase,
    parse_implementation_response,
    validate_implementation_path,
)
from state import PhaseName, PhaseRecord, State
from tools.base import SubprocessOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


class ScriptedLlmClient:
    """LlmClient that yields a sequence of canned responses."""

    def __init__(
        self,
        responses: list[ChatResponse | LlmError],
        *,
        model: str = "impl-model",
    ) -> None:
        self._responses = list(responses)
        self.calls: list[Sequence[ChatMessage]] = []
        self.model = model

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        del max_tokens, temperature
        self.calls.append(list(messages))
        if not self._responses:
            return _ok_response("## FILE: src/empty.py\n```python\nx = 0\n```\n", model=self.model)
        item = self._responses.pop(0)
        if isinstance(item, LlmError):
            raise item
        return item

    async def aclose(self) -> None:
        pass


class ScriptedRunner:
    """SubprocessRunner that returns scripted outcomes for `make check`."""

    def __init__(self, make_outcomes: list[SubprocessOutcome]) -> None:
        self.calls: list[list[str]] = []
        self._make_outcomes = list(make_outcomes)
        self.head_sha = "deadbeef" * 5

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
        if argv_list[:1] == ["make"]:
            if not self._make_outcomes:
                return SubprocessOutcome(returncode=0, stdout="", stderr="")
            return self._make_outcomes.pop(0)
        if argv_list[:2] == ["git", "rev-parse"]:
            return SubprocessOutcome(returncode=0, stdout=self.head_sha + "\n", stderr="")
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


def _ok_response(content: str, *, model: str = "impl-model") -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(input_tokens=50, output_tokens=80),
        model=model,
        finish_reason=FinishReason.STOP,
        duration_ms=10.0,
    )


def _state() -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.IMPLEMENTATION,
        phases=[PhaseRecord(name=PhaseName.IMPLEMENTATION)],
    )


def _make_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "src").mkdir()
    work_dir = workspace / ".agent_work" / "demo"
    work_dir.mkdir(parents=True)
    ticket = workspace / "ticket.md"
    ticket.write_text(
        "---\nid: demo\ntitle: Add subtract\n---\n\n## Description\n\nx.\n\n## Acceptance Criteria\n\n- AC-1: x\n",
        encoding="utf-8",
    )
    (work_dir / "plan.md").write_text("Plan body.", encoding="utf-8")
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path) -> PhaseContext:
    return PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket_path))


_VALID_BLOCK = "## FILE: src/calc.py\n```python\ndef subtract(a, b):\n    return a - b\n```\n"
_LOCKED_BLOCK = "## FILE: tests/test_locked.py\n```python\nx = 1\n```\n"


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_implementation_response_extracts_blocks() -> None:
    """A valid response yields one EditedFile."""
    files = parse_implementation_response(_VALID_BLOCK)
    assert files[0].path == "src/calc.py"
    assert "subtract" in files[0].content


def test_parse_implementation_response_rejects_locked_test_path() -> None:
    """A path matching `tests/test_*.py` is rejected with ValueError."""
    with pytest.raises(ValueError, match="locked"):
        parse_implementation_response(_LOCKED_BLOCK)


def test_parse_implementation_response_rejects_no_blocks() -> None:
    """A response with no blocks is rejected."""
    with pytest.raises(ValueError, match="no '## FILE:'"):
        parse_implementation_response("just prose")


def test_parse_implementation_response_rejects_duplicate_paths() -> None:
    """Two blocks targeting the same path are rejected."""
    dup = "## FILE: src/x.py\n```python\nx=1\n```\n\n## FILE: src/x.py\n```python\ny=2\n```\n"
    with pytest.raises(ValueError, match="duplicate"):
        parse_implementation_response(dup)


def test_validate_implementation_path_allows_conftest_and_testdata() -> None:
    """conftest.py and tests/testdata/ writes are allowed."""
    validate_implementation_path("tests/conftest.py")
    validate_implementation_path("tests/testdata/sample.json")


def test_validate_implementation_path_rejects_traversal_and_absolute() -> None:
    """`..` and absolute paths are rejected."""
    with pytest.raises(ValueError, match="escapes"):
        validate_implementation_path("../etc/passwd")
    with pytest.raises(ValueError, match="escapes"):
        validate_implementation_path("/etc/passwd")


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_without_llm_client_is_noop_continue(tmp_path: Path) -> None:
    """No LLM client: phase logs and returns CONTINUE."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    phase = ImplementationPhase()

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE


async def test_run_converges_when_first_make_check_passes(tmp_path: Path) -> None:
    """`make check` returncode 0 on iteration 1 yields CONTINUE and a commit."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    llm = ScriptedLlmClient([_ok_response(_VALID_BLOCK)])
    runner = ScriptedRunner([SubprocessOutcome(returncode=0, stdout="all green", stderr="")])
    phase = ImplementationPhase(llm_client=llm, workspace=workspace, runner=runner)
    ctx = _ctx(work_dir, ticket)

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    assert (workspace / "src" / "calc.py").exists()
    payload = json.loads((work_dir / IMPLEMENTATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["final_status"] == "converged"
    assert payload["commit_sha"] == runner.head_sha
    assert ctx.state.implementation_commit_sha == runner.head_sha
    # git add + git commit + git rev-parse should each have been called.
    assert any(c[:2] == ["git", "add"] for c in runner.calls)
    assert any(c[:2] == ["git", "commit"] for c in runner.calls)


async def test_run_converges_after_a_few_iterations(tmp_path: Path) -> None:
    """The loop persists multiple iterations and converges on the last one."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    responses = [
        _ok_response("## FILE: src/v1.py\n```python\nx = 1\n```\n"),
        _ok_response("## FILE: src/v2.py\n```python\nx = 2\n```\n"),
        _ok_response("## FILE: src/v3.py\n```python\nx = 3\n```\n"),
    ]
    make_outcomes = [
        SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_a\n", stderr=""),
        SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_a\n", stderr=""),
        SubprocessOutcome(returncode=0, stdout="all green", stderr=""),
    ]
    llm = ScriptedLlmClient(responses)
    runner = ScriptedRunner(make_outcomes)
    phase = ImplementationPhase(llm_client=llm, workspace=workspace, runner=runner)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    payload = json.loads((work_dir / IMPLEMENTATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["final_status"] == "converged"
    assert len(payload["iterations"]) == 3


async def test_run_halts_exhausted_on_max_iterations(tmp_path: Path) -> None:
    """When max_iterations is hit without success, halt with HALT_EXHAUSTED."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    # Three failing iterations, but each has a different signature so stagnation does not fire.
    responses = [
        _ok_response("## FILE: src/v1.py\n```python\nx = 1\n```\n"),
        _ok_response("## FILE: src/v2.py\n```python\nx = 2\n```\n"),
        _ok_response("## FILE: src/v3.py\n```python\nx = 3\n```\n"),
    ]
    make_outcomes = [
        SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_a\n", stderr=""),
        SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_b\n", stderr=""),
        SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_c\n", stderr=""),
    ]
    llm = ScriptedLlmClient(responses)
    runner = ScriptedRunner(make_outcomes)
    phase = ImplementationPhase(
        llm_client=llm,
        workspace=workspace,
        runner=runner,
        max_iterations=3,
        stagnation_threshold=99,
    )

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_EXHAUSTED
    assert "max iterations" in outcome.message


async def test_run_halts_on_stagnation(tmp_path: Path) -> None:
    """Same failing-test signature for `stagnation_threshold` rounds halts the loop."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    responses = [
        _ok_response("## FILE: src/v1.py\n```python\nx = 1\n```\n"),
        _ok_response("## FILE: src/v2.py\n```python\nx = 2\n```\n"),
        _ok_response("## FILE: src/v3.py\n```python\nx = 3\n```\n"),
    ]
    same_failure = SubprocessOutcome(returncode=1, stdout="FAILED tests/t.py::test_a\n", stderr="")
    runner = ScriptedRunner([same_failure, same_failure, same_failure])
    phase = ImplementationPhase(
        llm_client=ScriptedLlmClient(responses),
        workspace=workspace,
        runner=runner,
        max_iterations=10,
        stagnation_threshold=3,
    )

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_EXHAUSTED
    assert "stagnated" in outcome.message


async def test_run_handles_parse_error_iteration_then_recovers(tmp_path: Path) -> None:
    """A response with no FILE blocks is recorded and the loop continues."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    responses = [
        _ok_response("just prose, no blocks"),
        _ok_response(_VALID_BLOCK),
    ]
    runner = ScriptedRunner([SubprocessOutcome(returncode=0, stdout="ok", stderr="")])
    phase = ImplementationPhase(
        llm_client=ScriptedLlmClient(responses),
        workspace=workspace,
        runner=runner,
        max_iterations=5,
    )

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    payload = json.loads((work_dir / IMPLEMENTATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    # The first iteration recorded the parse error and did not run make check.
    assert payload["iterations"][0]["parse_error"]
    assert payload["iterations"][0]["check_returncode"] == -1
    # The second iteration succeeded.
    assert payload["iterations"][1]["check_returncode"] == 0


async def test_run_records_llm_error_and_halts_exhausted(tmp_path: Path) -> None:
    """An LlmError aborts the loop; the iteration is recorded with parse_error set."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    phase = ImplementationPhase(
        llm_client=ScriptedLlmClient([LlmError("endpoint down")]),
        workspace=workspace,
        runner=ScriptedRunner([]),
    )

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_EXHAUSTED
    payload = json.loads((work_dir / IMPLEMENTATION_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["iterations"][0]["parse_error"] == "endpoint down"


def test_default_stagnation_threshold_matches_spec() -> None:
    """The default threshold of 5 matches FR-008."""
    assert DEFAULT_STAGNATION_THRESHOLD == 5
