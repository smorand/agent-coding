"""Tests for the reviewer phase (FR-011)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, LlmError, TokenUsage
from phases.base import OutcomeKind, PhaseContext
from phases.review import (
    REVIEW_REPORT_FILENAME,
    ReviewPhase,
    ReviewVerdict,
    parse_review_response,
)
from state import PhaseName, PhaseRecord, State
from tools.base import SubprocessOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


class FakeLlmClient:
    """LlmClient test double."""

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
    """SubprocessRunner that returns canned outcomes per first-arg pattern."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.diff_out: str = "diff --git a/src/x.py b/src/x.py\n+added line\n"
        self.status_out: str = " M src/x.py\n"

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        del cwd, timeout, input_text
        self.calls.append(list(argv))
        if argv[1:2] == ["diff"]:
            return SubprocessOutcome(returncode=0, stdout=self.diff_out, stderr="")
        if argv[1:2] == ["status"]:
            return SubprocessOutcome(returncode=0, stdout=self.status_out, stderr="")
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


def _ok_response(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(input_tokens=300, output_tokens=200),
        model="reviewer-model",
        finish_reason=FinishReason.STOP,
        duration_ms=40.0,
    )


def _state(*, e2e_sha: str | None = None) -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.REVIEW,
        phases=[PhaseRecord(name=PhaseName.REVIEW)],
        e2e_commit_sha=e2e_sha,
    )


def _make_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    work_dir = workspace / ".agent_work" / "demo"
    work_dir.mkdir(parents=True)
    ticket = workspace / "ticket.md"
    ticket.write_text(
        "---\nid: demo\ntitle: x\n---\n\n## Description\n\nxyz.\n\n## Acceptance Criteria\n\n- AC-1: x\n",
        encoding="utf-8",
    )
    (work_dir / "plan.md").write_text("Plan body.", encoding="utf-8")
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path, *, e2e_sha: str | None = None) -> PhaseContext:
    return PhaseContext(state=_state(e2e_sha=e2e_sha), work_dir=work_dir, ticket_path=str(ticket_path))


_APPROVE_RESPONSE = (
    "## VERDICT\n\nAPPROVE\n\n"
    "## BLOCKING\n\nNone.\n\n"
    "## SUGGESTIONS\n\n- src/x.py:42 - consider renaming\n\n"
    "## SUMMARY\n\nLooks good. Tests cover all ACs.\n"
)

_REQUEST_CHANGES_RESPONSE = (
    "## VERDICT\n\nREQUEST_CHANGES\n\n"
    "## BLOCKING\n\n- CLAUDE.md:? - missing entry for new module\n- src/x.py:10 - regression risk\n\n"
    "## SUGGESTIONS\n\nNone.\n\n"
    "## SUMMARY\n\nNeeds doc update before merge.\n"
)


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_review_response_approve() -> None:
    """Approve response yields APPROVE verdict and one suggestion."""
    verdict, blocking, suggestions, summary = parse_review_response(_APPROVE_RESPONSE)
    assert verdict == ReviewVerdict.APPROVE
    assert blocking == ()
    assert len(suggestions) == 1
    assert suggestions[0].path == "src/x.py"
    assert suggestions[0].line == "42"
    assert "renaming" in suggestions[0].reason
    assert "Tests cover" in summary


def test_parse_review_response_request_changes() -> None:
    """Request-changes response yields the verdict and parses two blocking concerns."""
    verdict, blocking, suggestions, summary = parse_review_response(_REQUEST_CHANGES_RESPONSE)
    assert verdict == ReviewVerdict.REQUEST_CHANGES
    assert len(blocking) == 2
    assert blocking[0].path == "CLAUDE.md"
    assert blocking[0].line == "?"
    assert blocking[1].path == "src/x.py"
    assert suggestions == ()
    assert "doc update" in summary


def test_parse_review_response_rejects_missing_section() -> None:
    """A response missing one of the four sections raises ValueError."""
    bad = "## VERDICT\n\nAPPROVE\n\n## BLOCKING\n\nNone.\n\n## SUGGESTIONS\n\nNone.\n"
    with pytest.raises(ValueError, match="missing required section"):
        parse_review_response(bad)


def test_parse_review_response_rejects_unknown_verdict() -> None:
    """A verdict that is neither APPROVE nor REQUEST_CHANGES raises."""
    bad = "## VERDICT\n\nMAYBE\n\n## BLOCKING\n\nNone.\n\n## SUGGESTIONS\n\nNone.\n\n## SUMMARY\n\nx.\n"
    with pytest.raises(ValueError, match="unknown verdict"):
        parse_review_response(bad)


def test_parse_review_response_tolerates_freeform_concern() -> None:
    """A bullet that does not match path:line - reason is kept as path-less concern."""
    text = (
        "## VERDICT\n\nAPPROVE\n\n"
        "## BLOCKING\n\n- some free-form description without path\n\n"
        "## SUGGESTIONS\n\nNone.\n\n"
        "## SUMMARY\n\nx.\n"
    )
    _, blocking, _, _ = parse_review_response(text)
    assert len(blocking) == 1
    assert blocking[0].path == ""
    assert blocking[0].line == "?"
    assert "free-form" in blocking[0].reason


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_without_llm_client_is_noop_continue(tmp_path: Path) -> None:
    """No LLM client: phase returns CONTINUE without writing review.json."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    phase = ReviewPhase()

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    assert not (work_dir / REVIEW_REPORT_FILENAME).exists()


async def test_run_persists_review_json_on_approve(tmp_path: Path) -> None:
    """An APPROVE response persists review.json with verdict and metadata."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_APPROVE_RESPONSE))
    runner = FakeRunner()
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=runner)
    ctx = _ctx(work_dir, ticket, e2e_sha="abc123")

    outcome = await phase.run(ctx)

    assert outcome.kind == OutcomeKind.CONTINUE
    payload = json.loads((work_dir / REVIEW_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["verdict"] == "APPROVE"
    assert payload["blocking"] == []
    assert len(payload["suggestions"]) == 1
    assert payload["model"] == "reviewer-model"
    assert ctx.state.review_verdict == "APPROVE"


async def test_run_records_request_changes_verdict(tmp_path: Path) -> None:
    """A REQUEST_CHANGES verdict is persisted with all blocking concerns."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_REQUEST_CHANGES_RESPONSE))
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=FakeRunner())
    ctx = _ctx(work_dir, ticket, e2e_sha="deadbeef")

    await phase.run(ctx)

    payload = json.loads((work_dir / REVIEW_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert payload["verdict"] == "REQUEST_CHANGES"
    assert len(payload["blocking"]) == 2
    assert ctx.state.review_verdict == "REQUEST_CHANGES"


async def test_run_diffs_against_e2e_commit_when_present(tmp_path: Path) -> None:
    """When state.e2e_commit_sha is set, git diff <sha> is invoked."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_APPROVE_RESPONSE))
    runner = FakeRunner()
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=runner)

    await phase.run(_ctx(work_dir, ticket, e2e_sha="cafef00d"))

    diff_calls = [c for c in runner.calls if c[1:2] == ["diff"]]
    assert any("cafef00d" in c for c in diff_calls)


async def test_run_uses_plain_diff_when_no_e2e_commit(tmp_path: Path) -> None:
    """Without a recorded SHA, git diff is invoked without a target."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_APPROVE_RESPONSE))
    runner = FakeRunner()
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=runner)

    await phase.run(_ctx(work_dir, ticket))

    diff_calls = [c for c in runner.calls if c[1:2] == ["diff"]]
    assert diff_calls == [["git", "diff"]]


async def test_run_returns_halt_error_on_llm_failure(tmp_path: Path) -> None:
    """LlmError from the client maps to HALT_ERROR; review.json is not written."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(LlmError("endpoint unreachable"))
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=FakeRunner())

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "endpoint unreachable" in outcome.message
    assert not (work_dir / REVIEW_REPORT_FILENAME).exists()


async def test_run_returns_halt_error_on_malformed_response(tmp_path: Path) -> None:
    """A response missing required sections halts with HALT_ERROR."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response("just a paragraph"))
    phase = ReviewPhase(llm_client=fake, workspace=workspace, git_runner=FakeRunner())

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "malformed" in outcome.message
