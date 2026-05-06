"""End-to-end pipeline tests (subset of spec section 8).

These tests drive the agent-code CLI through a real Typer invocation
with the LLM, MCP, gh, and `make` external dependencies stubbed via the
shared fixtures in `tests/conftest.py`. The goal is to verify the
agent's overall pipeline shape (commit ordering, exit codes, persisted
artifacts) rather than the unit behavior of each phase, which is
covered by the per-phase test files.

Coverage map (spec ID -> test):
- E2E-001: test_e2e_001_happy_path_produces_pull_request
- E2E-002: test_e2e_002_incomplete_ticket_exits_with_dor_failed
- E2E-006: test_e2e_006_bootstrap_populates_empty_workspace
- E2E-023: test_e2e_023_e2e_tests_committed_before_implementation
- E2E-024: test_e2e_024_pr_body_matches_canonical_template
- E2E-025: test_e2e_025_unsatisfiable_infra_stops_pipeline
- E2E-026: test_e2e_026_e2e_commit_tampering_detected
- E2E-021: test_e2e_021_audit_trail_commit_per_phase

The remaining 20 spec E2E tests are exercised via per-phase unit tests
or are out of scope for this suite (timing/perf, bootstrap edge cases).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess  # nosec B404 - test-only fixture using static argv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from typer.testing import CliRunner

if TYPE_CHECKING:
    import pytest

from agent_code import app
from llm.base import ChatResponse, FinishReason, TokenUsage
from llm.openai_compat import OpenAICompatClient
from phases.base import OutcomeKind, PhaseContext
from phases.pr_creation import PrCreationPhase
from state import PhaseName, PhaseRecord, State
from tests.conftest import git_init_repo, minimal_valid_yaml
from tools.base import SubprocessOutcome
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage

cli = CliRunner()


HAPPY_TICKET_BODY = (
    "---\n"
    "id: add-subtract\n"
    "title: Add a subtract function to the calc module\n"
    "author: Tester\n"
    "---\n\n"
    "## Description\n\n"
    "Implement a subtract(a, b) function in src/calc.py that mirrors the existing add(a, b).\n\n"
    "## Acceptance Criteria\n\n"
    "- AC-1: subtract(3, 1) returns 2.\n"
    "- AC-2: subtract handles zero correctly.\n\n"
    "## Infrastructure\n\nNone.\n"
)

PG_TICKET_BODY = (
    "---\n"
    "id: pg-feature\n"
    "title: Persist records via Postgres\n"
    "author: Tester\n"
    "---\n\n"
    "## Description\n\n"
    "Persist the user records to a Postgres database; existing records must continue to work.\n\n"
    "## Acceptance Criteria\n\n"
    "- AC-1: records survive a process restart.\n\n"
    "## Infrastructure\n\nrequires Postgres 15.\n"
)

INCOMPLETE_TICKET_BODY = "---\nid: incomplete\ntitle: Incomplete ticket\n---\n\n## Description\n\nshort.\n"


def _make_python_workspace(tmp_path: Path, *, ticket_body: str, ticket_name: str) -> tuple[Path, Path]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (workspace / "src").mkdir()
    (workspace / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "tests").mkdir()
    git_init_repo(workspace)
    ticket = workspace / ticket_name
    ticket.write_text(ticket_body, encoding="utf-8")
    return workspace, ticket


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.yaml"
    template = tmp_path / "template"
    template.mkdir()
    cfg.write_text(minimal_valid_yaml(template_path=str(template)), encoding="utf-8")
    return cfg


def _run_cli(workspace: Path, ticket: Path, config: Path) -> object:
    return cli.invoke(
        app,
        [
            "run",
            str(ticket),
            "--workspace",
            str(workspace),
            "--config",
            str(config),
        ],
    )


def _git_log(workspace: Path) -> list[str]:
    """Return commit subjects from oldest to newest."""
    result = subprocess.run(  # nosec B603, B607
        ["git", "log", "--reverse", "--pretty=format:%s"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


# ──────────────────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_001_happy_path_produces_pull_request(tmp_path: Path, stub_llm: None, stub_subprocess: None) -> None:
    """E2E-001: well-formed ticket on a Python repo runs the pipeline to PR creation."""
    del stub_llm, stub_subprocess
    workspace, ticket = _make_python_workspace(tmp_path, ticket_body=HAPPY_TICKET_BODY, ticket_name="add-subtract.md")

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 0, result.stdout + result.stderr
    work_dir = workspace / ".agent_work" / "add-subtract"
    # Every phase persisted its report.
    for filename in (
        "classification.json",
        "dor_report.json",
        "comprehension.json",
        "planning.json",
        "e2e_writing.json",
        "implementation.json",
        "review.json",
        "pr_creation.json",
    ):
        assert (work_dir / filename).exists(), f"missing {filename}"
    # The pr_creation.json carries the captured PR URL.
    pr_payload = json.loads((work_dir / "pr_creation.json").read_text(encoding="utf-8"))
    assert pr_payload["pr_url"].startswith("https://github.com/")


# ──────────────────────────────────────────────────────────────────────────────
# DoR failure (E2E-002)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_002_incomplete_ticket_exits_with_dor_failed(tmp_path: Path) -> None:
    """E2E-002: a ticket missing AC fails the DoR gate with exit 1 and a comment."""
    workspace, ticket = _make_python_workspace(
        tmp_path, ticket_body=INCOMPLETE_TICKET_BODY, ticket_name="incomplete.md"
    )

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 1
    body = ticket.read_text(encoding="utf-8")
    assert "<!-- agent-code DoR report" in body
    assert "**Status**: NOT_READY" in body


# ──────────────────────────────────────────────────────────────────────────────
# Bootstrap (E2E-006)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_006_bootstrap_populates_empty_workspace(tmp_path: Path, stub_llm: None, stub_subprocess: None) -> None:
    """E2E-006: an empty repo is bootstrapped from the configured template."""
    del stub_llm, stub_subprocess
    template = tmp_path / "template"
    (template / "src").mkdir(parents=True)
    (template / "tests").mkdir()
    (template / ".template_version").write_text("0.1.0\n", encoding="utf-8")
    (template / "pyproject.toml").write_text("[project]\nname = '__PROJECT_NAME__'\n", encoding="utf-8")
    (template / "src" / "__PROJECT_ENTRY__.py").write_text("# __PROJECT_NAME__\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    git_init_repo(workspace)
    ticket = workspace / "boot.md"
    ticket.write_text(HAPPY_TICKET_BODY, encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(minimal_valid_yaml(template_path=str(template)), encoding="utf-8")

    result = _run_cli(workspace, ticket, cfg)

    assert result.exit_code == 0, result.stdout + result.stderr
    assert (workspace / "pyproject.toml").exists()
    classification = json.loads(
        (workspace / ".agent_work" / "boot" / "classification.json").read_text(encoding="utf-8")
    )
    assert classification["bootstrap"]["materialized_files"]


# ──────────────────────────────────────────────────────────────────────────────
# E2E commit ordering (E2E-023)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_023_e2e_tests_committed_before_implementation(
    tmp_path: Path, stub_llm: None, stub_subprocess: None
) -> None:
    """E2E-023: the `Add E2E tests for <id>` commit precedes any implementation commit."""
    del stub_llm, stub_subprocess
    workspace, ticket = _make_python_workspace(tmp_path, ticket_body=HAPPY_TICKET_BODY, ticket_name="add-subtract.md")

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 0, result.stdout + result.stderr
    log = _git_log(workspace)
    e2e_idx = next(i for i, s in enumerate(log) if "Add E2E tests for" in s)
    impl_idx = next((i for i, s in enumerate(log) if s.startswith("Implement ")), None)
    assert impl_idx is None or e2e_idx < impl_idx


# ──────────────────────────────────────────────────────────────────────────────
# PR template format (E2E-024)
# ──────────────────────────────────────────────────────────────────────────────

_PR_BODY_REGEX = re.compile(
    r"^## User Story\n.*\n## Acceptance Criteria\n.*\n## Approach\n.*\n## E2E Tests\n.*\n## Notable Decisions\n.*\n## Out of Scope\n.*$",
    re.DOTALL,
)


def test_e2e_024_pr_body_matches_canonical_template(
    tmp_path: Path, stub_llm: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2E-024: the body sent to `gh pr create` matches the canonical template regex."""
    del stub_llm
    captured: dict[str, str] = {}
    real_run = AsyncSubprocessRunner.run

    async def fake_run(
        self: AsyncSubprocessRunner,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = 30.0,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        argv_list = list(argv)
        if argv_list[:3] == ["gh", "pr", "create"]:
            body_idx = argv_list.index("--body")
            captured["body"] = argv_list[body_idx + 1]
            return SubprocessOutcome(returncode=0, stdout="https://github.com/x/y/pull/1\n", stderr="")
        if argv_list[:1] == ["gh"]:
            return SubprocessOutcome(returncode=0, stdout="", stderr="")
        if argv_list[:2] == ["git", "push"]:
            return SubprocessOutcome(returncode=0, stdout="", stderr="")
        if argv_list[:1] == ["make"]:
            return SubprocessOutcome(returncode=0, stdout="ok\n", stderr="")
        return await real_run(self, argv, cwd=cwd, timeout=timeout, input_text=input_text)

    monkeypatch.setattr(AsyncSubprocessRunner, "run", fake_run)
    workspace, ticket = _make_python_workspace(tmp_path, ticket_body=HAPPY_TICKET_BODY, ticket_name="add-subtract.md")

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 0
    assert "body" in captured
    assert _PR_BODY_REGEX.match(captured["body"]), captured["body"]


# ──────────────────────────────────────────────────────────────────────────────
# Infrastructure unsatisfiable (E2E-025)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_025_unsatisfiable_infra_stops_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_subprocess: None
) -> None:
    """E2E-025: a Postgres requirement without docker-compose halts at planning with exit 1.

    Stubs the planning LLM so that infra_needs.md declares a Postgres
    requirement that is not satisfied by the workspace (no compose file).
    """
    del stub_subprocess

    async def fake_complete(
        _self: OpenAICompatClient,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        del max_tokens, temperature
        text = next((m.content for m in messages if m.role.value == "system"), "").lower()
        if "planning phase" in text:
            content = "## PLAN\n\nUse postgres.\n\n## TODO\n\n- [ ] connect\n\n## INFRA NEEDS\n\n- Postgres 15\n"
        else:
            content = "stub"
        return ChatResponse(
            content=content,
            usage=TokenUsage(input_tokens=10, output_tokens=4),
            model="stub-model",
            finish_reason=FinishReason.STOP,
            duration_ms=1.0,
        )

    monkeypatch.setattr(OpenAICompatClient, "complete", fake_complete)
    workspace, ticket = _make_python_workspace(tmp_path, ticket_body=PG_TICKET_BODY, ticket_name="pg-feature.md")

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 1
    body = ticket.read_text(encoding="utf-8")
    assert "agent-code planning report" in body
    assert "'Postgres 15' is not provisioned" in body


# ──────────────────────────────────────────────────────────────────────────────
# E2E commit tampering (E2E-026)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_026_e2e_commit_tampering_detected(tmp_path: Path) -> None:
    """E2E-026: when the recorded SHA is not in history, PR creation halts with exit 2."""
    workspace, _ = _make_python_workspace(tmp_path, ticket_body=HAPPY_TICKET_BODY, ticket_name="add-subtract.md")
    work_dir = workspace / ".agent_work" / "demo"
    work_dir.mkdir(parents=True)
    ticket_path = workspace / "ticket.md"
    ticket_path.write_text(HAPPY_TICKET_BODY, encoding="utf-8")
    now = datetime.now(UTC)
    state = State(
        ticket_id="demo",
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.PR_CREATION,
        phases=[PhaseRecord(name=PhaseName.PR_CREATION)],
        e2e_commit_sha="0" * 40,  # SHA that does not exist in the repo
        review_verdict="APPROVE",
    )
    ctx = PhaseContext(state=state, work_dir=work_dir, ticket_path=str(ticket_path))
    phase = PrCreationPhase(workspace=workspace)

    outcome = asyncio.run(phase.run(ctx))

    assert outcome.kind == OutcomeKind.HALT_EXHAUSTED
    assert "modified after lock" in outcome.message


# ──────────────────────────────────────────────────────────────────────────────
# Audit trail commit per phase (E2E-021)
# ──────────────────────────────────────────────────────────────────────────────


def test_e2e_021_audit_trail_commit_per_phase(tmp_path: Path, stub_llm: None, stub_subprocess: None) -> None:
    """E2E-021: every phase produces one `agent-code: phase <name>` commit."""
    del stub_llm, stub_subprocess
    workspace, ticket = _make_python_workspace(tmp_path, ticket_body=HAPPY_TICKET_BODY, ticket_name="add-subtract.md")

    result = _run_cli(workspace, ticket, _config(tmp_path))

    assert result.exit_code == 0, result.stdout + result.stderr
    log = _git_log(workspace)
    audit_commits = [s for s in log if s.startswith("agent-code: phase ")]
    # Every phase that ran should have produced one audit-trail commit.
    # The pipeline has 8 phases; classification through pr_creation each commit once.
    phases_in_log = {s.removeprefix("agent-code: phase ") for s in audit_commits}
    expected_phases = {
        "classification",
        "dor",
        "comprehension",
        "planning",
        "e2e_writing",
        "implementation",
        "review",
        "pr_creation",
    }
    assert expected_phases <= phases_in_log
