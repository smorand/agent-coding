"""Tests for the comprehension phase (FR-005)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, LlmError, TokenUsage
from phases.base import OutcomeKind, PhaseContext
from phases.comprehension import (
    COMPREHENSION_REPORT_FILENAME,
    ComprehensionPhase,
    _extract_keywords,
    _select_agent_docs,
    _truncate,
)
from state import PhaseName, PhaseRecord, State

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


class FakeLlmClient:
    """Test double for `LlmClient` that records calls and returns canned responses."""

    def __init__(self, response: ChatResponse | LlmError) -> None:
        self._response = response
        self.calls: list[Sequence[ChatMessage]] = []
        self.closed = False

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
        self.closed = True


def _ok_response(content: str = "## Context\n\nProject is X.\n") -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(input_tokens=42, output_tokens=18),
        model="test-model",
        finish_reason=FinishReason.STOP,
        duration_ms=12.0,
    )


def _state(ticket_id: str = "demo") -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id=ticket_id,
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.COMPREHENSION,
        phases=[PhaseRecord(name=PhaseName.COMPREHENSION)],
    )


def _make_workspace(tmp_path: Path, *, ticket_id: str = "demo") -> tuple[Path, Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    work_dir = workspace / ".agent_work" / ticket_id
    work_dir.mkdir(parents=True)
    ticket = workspace / "ticket.md"
    ticket.write_text(
        "---\nid: demo\ntitle: Add subtract feature\n---\n\n"
        "## Description\n\nImplement subtract using bootstrap and tooling.\n\n"
        "## Acceptance Criteria\n\n- AC-1: subtract works.\n",
        encoding="utf-8",
    )
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path) -> PhaseContext:
    return PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket_path))


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────────────────


def test_extract_keywords_skips_stopwords_and_dedupes() -> None:
    """Stopwords are removed; duplicates collapsed; tokens lowercased."""
    keywords = _extract_keywords("Bootstrap and tooling for tooling and bootstrap")
    assert "bootstrap" in keywords
    assert "tooling" in keywords
    assert "and" not in keywords
    assert len(keywords) == 2


def test_extract_keywords_returns_empty_on_empty_text() -> None:
    """Empty text yields an empty keyword set."""
    assert _extract_keywords("") == frozenset()


def test_extract_keywords_caps_to_limit() -> None:
    """The keyword set never exceeds the configured limit."""
    text = " ".join(f"word{i}" for i in range(50))
    keywords = _extract_keywords(text, limit=5)
    assert len(keywords) == 5


def test_select_agent_docs_prefers_keyword_overlap(tmp_path: Path) -> None:
    """Files whose stem overlaps with keywords come first."""
    docs = tmp_path / ".agent_docs"
    docs.mkdir()
    (docs / "bootstrap.md").write_text("x", encoding="utf-8")
    (docs / "tooling.md").write_text("x", encoding="utf-8")
    (docs / "unrelated.md").write_text("x", encoding="utf-8")
    keywords = frozenset({"bootstrap"})
    selected = list(_select_agent_docs(docs, keywords, cap=3))
    assert selected[0].name == "bootstrap.md"
    assert {p.name for p in selected[1:]} == {"tooling.md", "unrelated.md"}


def test_select_agent_docs_caps_results(tmp_path: Path) -> None:
    """Selection respects the cap."""
    docs = tmp_path / ".agent_docs"
    docs.mkdir()
    for i in range(5):
        (docs / f"doc{i}.md").write_text("x", encoding="utf-8")
    selected = list(_select_agent_docs(docs, frozenset(), cap=2))
    assert len(selected) == 2


def test_select_agent_docs_empty_dir_returns_empty(tmp_path: Path) -> None:
    """An empty docs directory yields no selection."""
    docs = tmp_path / ".agent_docs"
    docs.mkdir()
    assert tuple(_select_agent_docs(docs, frozenset(), cap=3)) == ()


def test_truncate_returns_short_text_unchanged() -> None:
    """Text under the cap is returned verbatim."""
    text, used = _truncate("hello", 1000)
    assert text == "hello"
    assert used == len(b"hello")


def test_truncate_handles_zero_budget() -> None:
    """A zero or negative budget returns an empty string and zero bytes."""
    text, used = _truncate("hello", 0)
    assert text == ""
    assert used == 0


def test_truncate_appends_marker_when_clipped() -> None:
    """Text over the cap gets clipped and gains a `[truncated]` marker."""
    long = "a" * 100
    clipped, used = _truncate(long, 20)
    assert clipped.startswith("a" * 20)
    assert "[truncated]" in clipped
    assert used == 20


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_without_llm_client_is_noop_continue(tmp_path: Path) -> None:
    """When no LLM client is configured, the phase logs and returns CONTINUE."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    phase = ComprehensionPhase()

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    assert not (work_dir / COMPREHENSION_REPORT_FILENAME).exists()


async def test_run_calls_llm_with_system_and_user_messages(tmp_path: Path) -> None:
    """The LLM call includes a system prompt and a user prompt with sources."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response())
    phase = ComprehensionPhase(llm_client=fake)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    assert len(fake.calls) == 1
    messages = fake.calls[0]
    assert len(messages) == 2
    assert messages[0].role.value == "system"
    assert "comprehension phase" in messages[0].content.lower()
    assert messages[1].role.value == "user"
    assert "Add subtract feature" in messages[1].content  # ticket title appears


async def test_run_persists_comprehension_json_with_metadata(tmp_path: Path) -> None:
    """The persisted JSON contains summary, model, token usage, and source list."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response("## Context\n\nstub.\n"))
    phase = ComprehensionPhase(llm_client=fake)

    await phase.run(_ctx(work_dir, ticket))

    persisted = work_dir / COMPREHENSION_REPORT_FILENAME
    assert persisted.exists()
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    assert payload["summary"] == "## Context\n\nstub.\n"
    assert payload["model"] == "test-model"
    assert payload["input_tokens"] == 42
    assert payload["output_tokens"] == 18
    assert "generated_at" in payload
    labels = {s["label"] for s in payload["sources"]}
    assert "ticket" in labels


async def test_run_includes_claude_md_in_sources_when_present(tmp_path: Path) -> None:
    """A workspace CLAUDE.md is read and included as a source."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    (workspace / "CLAUDE.md").write_text("# Project\n\nproject conventions.\n", encoding="utf-8")
    fake = FakeLlmClient(_ok_response())
    phase = ComprehensionPhase(llm_client=fake, workspace=workspace)

    await phase.run(_ctx(work_dir, ticket))

    payload = json.loads((work_dir / COMPREHENSION_REPORT_FILENAME).read_text(encoding="utf-8"))
    labels = {s["label"] for s in payload["sources"]}
    assert "claude_md" in labels


async def test_run_includes_relevant_agent_docs_first(tmp_path: Path) -> None:
    """Agent docs whose stem matches ticket keywords appear in the source list."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    docs = workspace / ".agent_docs"
    docs.mkdir()
    (docs / "bootstrap.md").write_text("# bootstrap rules\n", encoding="utf-8")
    (docs / "unrelated.md").write_text("# something else\n", encoding="utf-8")
    fake = FakeLlmClient(_ok_response())
    phase = ComprehensionPhase(llm_client=fake, workspace=workspace, agent_docs_cap=2)

    await phase.run(_ctx(work_dir, ticket))

    payload = json.loads((work_dir / COMPREHENSION_REPORT_FILENAME).read_text(encoding="utf-8"))
    paths = [s["path"] for s in payload["sources"] if s["label"] == "agent_docs"]
    assert any(p.endswith("bootstrap.md") for p in paths)


async def test_run_returns_halt_error_on_llm_failure(tmp_path: Path) -> None:
    """A LlmError from the client is converted to HALT_ERROR with the message."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(LlmError("endpoint unreachable"))
    phase = ComprehensionPhase(llm_client=fake)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "endpoint unreachable" in outcome.message
    assert not (work_dir / COMPREHENSION_REPORT_FILENAME).exists()


async def test_run_caps_total_input_bytes(tmp_path: Path) -> None:
    """Total input across all sources stays within the configured cap."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    (workspace / "CLAUDE.md").write_text("a" * 100_000, encoding="utf-8")
    docs = workspace / ".agent_docs"
    docs.mkdir()
    (docs / "x.md").write_text("b" * 100_000, encoding="utf-8")
    fake = FakeLlmClient(_ok_response())
    phase = ComprehensionPhase(
        llm_client=fake,
        workspace=workspace,
        per_file_cap_bytes=2_000,
        total_cap_bytes=5_000,
    )

    await phase.run(_ctx(work_dir, ticket))

    payload = json.loads((work_dir / COMPREHENSION_REPORT_FILENAME).read_text(encoding="utf-8"))
    total_used = sum(s["bytes_used"] for s in payload["sources"])
    assert total_used <= 5_000


@pytest.mark.parametrize("missing_only", [True])
async def test_run_handles_missing_ticket_gracefully(tmp_path: Path, missing_only: bool) -> None:
    """A missing ticket file produces an empty ticket excerpt without raising."""
    del missing_only
    workspace = tmp_path / "ws"
    workspace.mkdir()
    work_dir = workspace / ".agent_work" / "demo"
    work_dir.mkdir(parents=True)
    ticket = workspace / "absent.md"  # does not exist
    fake = FakeLlmClient(_ok_response())
    phase = ComprehensionPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    assert (work_dir / COMPREHENSION_REPORT_FILENAME).exists()
