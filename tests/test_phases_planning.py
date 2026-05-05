"""Tests for the planning phase (FR-006)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, LlmError, TokenUsage
from phases.base import OutcomeKind, PhaseContext
from phases.planning import (
    INFRA_NEEDS_FILENAME,
    PLAN_FILENAME,
    PLANNING_REPORT_FILENAME,
    TODO_FILENAME,
    InfraIssue,
    PlanningPhase,
    compose_declares_service,
    detect_service,
    find_compose_file,
    format_infra_comment,
    parse_infra_needs,
    parse_planning_response,
    validate_infra,
)
from state import PhaseName, PhaseRecord, State

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


class FakeLlmClient:
    """Test double for LlmClient with canned responses."""

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


def _ok_response(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        usage=TokenUsage(input_tokens=120, output_tokens=80),
        model="planner-model",
        finish_reason=FinishReason.STOP,
        duration_ms=20.0,
    )


def _state() -> State:
    now = datetime.now(UTC)
    return State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.PLANNING,
        phases=[PhaseRecord(name=PhaseName.PLANNING)],
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
        "## Acceptance Criteria\n\n- AC-1: subtract works.\n",
        encoding="utf-8",
    )
    return workspace, work_dir, ticket


def _ctx(work_dir: Path, ticket_path: Path) -> PhaseContext:
    return PhaseContext(state=_state(), work_dir=work_dir, ticket_path=str(ticket_path))


_RESPONSE_OK_NO_INFRA = (
    "## PLAN\n\n"
    "Implement subtract in src/calc.py. Add unit test coverage. ~40 LoC.\n\n"
    "## TODO\n\n"
    "- [ ] Add subtract function to src/calc.py\n"
    "- [ ] Add unit tests in tests/test_calc.py\n"
    "- [ ] Run make check\n\n"
    "## INFRA NEEDS\n\n"
    "None.\n"
)

_RESPONSE_OK_WITH_POSTGRES = (
    "## PLAN\n\nUse Postgres to persist the records.\n\n"
    "## TODO\n\n- [ ] connect to db\n- [ ] migrate schema\n\n"
    "## INFRA NEEDS\n\n- Postgres 15\n- Environment variable: DATABASE_URL\n"
)


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers: parse_planning_response
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_planning_response_extracts_three_sections() -> None:
    """A well-formed response yields the three sections trimmed of headers."""
    artifacts = parse_planning_response(_RESPONSE_OK_NO_INFRA)
    assert "src/calc.py" in artifacts.plan
    assert "subtract function" in artifacts.todo
    assert artifacts.infra_needs.startswith("None")


def test_parse_planning_response_rejects_missing_section() -> None:
    """A response missing one of the three headers raises ValueError."""
    bad = "## PLAN\n\nx\n\n## TODO\n\n- [ ] y\n"
    with pytest.raises(ValueError, match="missing required section"):
        parse_planning_response(bad)


def test_parse_planning_response_rejects_out_of_order_sections() -> None:
    """Sections must appear in the canonical order PLAN, TODO, INFRA NEEDS."""
    bad = "## TODO\n\n- [ ] x\n\n## PLAN\n\np\n\n## INFRA NEEDS\n\nNone.\n"
    with pytest.raises(ValueError, match="must appear in order"):
        parse_planning_response(bad)


def test_parse_planning_response_rejects_empty_section() -> None:
    """Sections with no content raise ValueError."""
    bad = "## PLAN\n\nplan\n\n## TODO\n\n\n## INFRA NEEDS\n\nNone.\n"
    with pytest.raises(ValueError, match="non-empty content"):
        parse_planning_response(bad)


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers: parse_infra_needs / detect_service / compose checks
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_infra_needs_returns_empty_for_none() -> None:
    """A 'None.' body returns no requirements."""
    assert parse_infra_needs("None.") == ()
    assert parse_infra_needs("none") == ()
    assert parse_infra_needs("") == ()


def test_parse_infra_needs_extracts_bullets() -> None:
    """Hyphen and asterisk bullets are recognized; surrounding whitespace stripped."""
    text = "- Postgres 15\n* Redis 7\n  - indented dash\n"
    items = parse_infra_needs(text)
    assert "Postgres 15" in items
    assert "Redis 7" in items


def test_detect_service_finds_known_service_case_insensitive() -> None:
    """Known service names are matched ignoring case and version suffixes."""
    assert detect_service("Postgres 15") == "postgres"
    assert detect_service("requires REDIS for caching") == "redis"
    assert detect_service("MongoDB 7.0") == "mongodb"


def test_detect_service_returns_none_when_no_known_service_mentioned() -> None:
    """Unknown infra (e.g., env vars) returns None."""
    assert detect_service("Environment variable: API_TOKEN") is None
    assert detect_service("Some random requirement") is None


def test_find_compose_file_returns_first_existing_candidate(tmp_path: Path) -> None:
    """When `compose.yml` exists it is preferred over the absent default name."""
    (tmp_path / "compose.yml").write_text("services:\n", encoding="utf-8")
    assert find_compose_file(tmp_path) == tmp_path / "compose.yml"


def test_find_compose_file_returns_none_when_absent(tmp_path: Path) -> None:
    """No compose file in the workspace returns None."""
    assert find_compose_file(tmp_path) is None


def test_compose_declares_service_detects_token(tmp_path: Path) -> None:
    """The textual matcher finds a service declared by name in compose."""
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  postgres:\n    image: postgres:15\n", encoding="utf-8")
    assert compose_declares_service(compose, "postgres")
    assert not compose_declares_service(compose, "redis")


# ──────────────────────────────────────────────────────────────────────────────
# Pure helpers: validate_infra
# ──────────────────────────────────────────────────────────────────────────────


def test_validate_infra_returns_empty_when_no_requirements(tmp_path: Path) -> None:
    """A 'None.' infra body produces no issues regardless of compose presence."""
    assert validate_infra(tmp_path, "None.") == ()


def test_validate_infra_flags_postgres_when_no_compose(tmp_path: Path) -> None:
    """A Postgres requirement without docker-compose yields one issue."""
    issues = validate_infra(tmp_path, "- Postgres 15\n")
    assert len(issues) == 1
    assert issues[0].service == "postgres"
    assert "Postgres 15" in issues[0].requirement
    assert "no docker-compose" in issues[0].reason.lower()


def test_validate_infra_passes_when_compose_declares_service(tmp_path: Path) -> None:
    """A service declared in compose is not flagged."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  postgres:\n    image: postgres:15\n", encoding="utf-8")
    assert validate_infra(tmp_path, "- Postgres 15\n") == ()


def test_validate_infra_ignores_unknown_requirement_types(tmp_path: Path) -> None:
    """Requirements without a recognized service token are not flagged."""
    issues = validate_infra(tmp_path, "- Environment variable: API_TOKEN\n")
    assert issues == ()


def test_validate_infra_flags_only_missing_services(tmp_path: Path) -> None:
    """Mixed list with one declared, one missing: only the missing one is flagged."""
    (tmp_path / "docker-compose.yml").write_text("services:\n  postgres:\n    image: postgres:15\n", encoding="utf-8")
    issues = validate_infra(tmp_path, "- Postgres 15\n- Redis 7\n")
    assert len(issues) == 1
    assert issues[0].service == "redis"


# ──────────────────────────────────────────────────────────────────────────────
# Comment formatting
# ──────────────────────────────────────────────────────────────────────────────


def test_format_infra_comment_lists_each_requirement() -> None:
    """The templated comment lists every issue with the canonical phrase."""
    issues = (
        InfraIssue(requirement="Postgres 15", service="postgres", reason="no compose"),
        InfraIssue(requirement="Redis 7", service="redis", reason="no compose"),
    )
    text = format_infra_comment(issues)
    assert "agent-code planning report" in text
    assert "'Postgres 15' is not provisioned" in text
    assert "'Redis 7' is not provisioned" in text
    assert "docker-compose service or update the ticket" in text


# ──────────────────────────────────────────────────────────────────────────────
# Phase behavior
# ──────────────────────────────────────────────────────────────────────────────


async def test_run_without_llm_client_is_noop_continue(tmp_path: Path) -> None:
    """No LLM client configured: phase logs and returns CONTINUE without writing artifacts."""
    _, work_dir, ticket = _make_workspace(tmp_path)
    phase = PlanningPhase()

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    assert not (work_dir / PLAN_FILENAME).exists()


async def test_run_persists_three_md_files_and_planning_json(tmp_path: Path) -> None:
    """A successful planning call writes plan.md, todo.md, infra_needs.md, planning.json."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_RESPONSE_OK_NO_INFRA))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    plan = (work_dir / PLAN_FILENAME).read_text(encoding="utf-8")
    todo = (work_dir / TODO_FILENAME).read_text(encoding="utf-8")
    infra = (work_dir / INFRA_NEEDS_FILENAME).read_text(encoding="utf-8")
    meta = json.loads((work_dir / PLANNING_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert "src/calc.py" in plan
    assert "subtract function" in todo
    assert infra.startswith("None")
    assert meta["model"] == "planner-model"
    assert meta["input_tokens"] == 120
    assert meta["output_tokens"] == 80
    assert "generated_at" in meta


async def test_run_includes_comprehension_summary_in_user_prompt(tmp_path: Path) -> None:
    """If comprehension.json is present, its summary is included in the user prompt."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    (work_dir / "comprehension.json").write_text(
        json.dumps({"summary": "## Context\n\nFrom comprehension."}),
        encoding="utf-8",
    )
    fake = FakeLlmClient(_ok_response(_RESPONSE_OK_NO_INFRA))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    await phase.run(_ctx(work_dir, ticket))

    user_msg = fake.calls[0][1].content
    assert "Comprehension report" in user_msg
    assert "From comprehension." in user_msg


async def test_run_works_without_comprehension_summary(tmp_path: Path) -> None:
    """Missing comprehension.json: the user prompt has only the ticket."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_RESPONSE_OK_NO_INFRA))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    user_msg = fake.calls[0][1].content
    assert "Ticket:" in user_msg
    assert "Comprehension report" not in user_msg


async def test_run_halts_dor_failed_when_infra_unsatisfiable(tmp_path: Path) -> None:
    """Postgres requirement without docker-compose halts with DOR_FAILED and a comment."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response(_RESPONSE_OK_WITH_POSTGRES))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_DOR_FAILED
    assert "Postgres 15" in outcome.message
    body = ticket.read_text(encoding="utf-8")
    assert "agent-code planning report" in body
    assert "'Postgres 15' is not provisioned" in body


async def test_run_continues_when_compose_declares_postgres(tmp_path: Path) -> None:
    """With docker-compose declaring postgres, the same plan proceeds normally."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    (workspace / "docker-compose.yml").write_text("services:\n  postgres:\n    image: postgres:15\n", encoding="utf-8")
    fake = FakeLlmClient(_ok_response(_RESPONSE_OK_WITH_POSTGRES))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.CONTINUE
    body = ticket.read_text(encoding="utf-8")
    assert "agent-code planning report" not in body


async def test_run_returns_halt_error_on_llm_failure(tmp_path: Path) -> None:
    """An LlmError from the client is converted to HALT_ERROR."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(LlmError("endpoint unreachable"))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "endpoint unreachable" in outcome.message
    assert not (work_dir / PLAN_FILENAME).exists()


async def test_run_returns_halt_error_on_malformed_response(tmp_path: Path) -> None:
    """A response missing required sections halts with HALT_ERROR."""
    workspace, work_dir, ticket = _make_workspace(tmp_path)
    fake = FakeLlmClient(_ok_response("just a paragraph, no sections"))
    phase = PlanningPhase(llm_client=fake, workspace=workspace)

    outcome = await phase.run(_ctx(work_dir, ticket))

    assert outcome.kind == OutcomeKind.HALT_ERROR
    assert "malformed" in outcome.message
