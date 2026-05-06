"""Shared test fixtures for the agent-code test suite.

The two fixtures below are shared by the CLI smoke tests
(`test_agent_code.py`) and the end-to-end pipeline tests
(`test_e2e_pipeline.py`). They monkey-patch:

- `OpenAICompatClient.complete` to return a phase-aware canned response,
  so the CLI runs without needing a real model endpoint.
- `AsyncSubprocessRunner.run` to intercept `git push`, every `gh` call,
  and `make ...`, while letting other git operations (init, add, commit,
  rev-parse, diff, status, cat-file) hit the real binary on the test
  workspace.
"""

from __future__ import annotations

import subprocess  # nosec B404 - test-only fixture using static argv
from typing import TYPE_CHECKING

import pytest

from llm.base import ChatResponse, FinishReason, TokenUsage
from llm.openai_compat import OpenAICompatClient
from tools.base import SubprocessOutcome
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from llm.base import ChatMessage


_STUB_PLANNING_RESPONSE = "## PLAN\n\nstub plan.\n\n## TODO\n\n- [ ] stub task\n\n## INFRA NEEDS\n\nNone.\n"
_STUB_E2E_RESPONSE = "## FILE: tests/test_stub.py\n```python\n# AC-1\ndef test_stub() -> None:\n    assert True\n```\n"
_STUB_REVIEW_RESPONSE = (
    "## VERDICT\n\nAPPROVE\n\n## BLOCKING\n\nNone.\n\n## SUGGESTIONS\n\nNone.\n\n## SUMMARY\n\nstub review ok.\n"
)
_STUB_IMPLEMENTATION_RESPONSE = (
    "## FILE: src/stub_impl.py\n```python\nx = 1\n```\n\n"
    "## FILE: CLAUDE.md\n```\n# stub-impl module added by the test fixture\n```\n"
)


@pytest.fixture
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub `OpenAICompatClient.complete` so CLI tests don't need a real LLM endpoint."""

    async def fake_complete(
        _self: OpenAICompatClient,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        del max_tokens, temperature
        system_text = next((m.content for m in messages if m.role.value == "system"), "").lower()
        if "planning phase" in system_text:
            content = _STUB_PLANNING_RESPONSE
        elif "end-to-end test writing" in system_text:
            content = _STUB_E2E_RESPONSE
        elif "reviewer phase" in system_text:
            content = _STUB_REVIEW_RESPONSE
        elif "implementation phase" in system_text:
            content = _STUB_IMPLEMENTATION_RESPONSE
        else:
            content = "## Context\n\nstub.\n"
        return ChatResponse(
            content=content,
            usage=TokenUsage(input_tokens=10, output_tokens=4),
            model="stub-model",
            finish_reason=FinishReason.STOP,
            duration_ms=1.0,
        )

    monkeypatch.setattr(OpenAICompatClient, "complete", fake_complete)


@pytest.fixture
def stub_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Intercept `git push`, any `gh ...`, and `make ...` invocations."""
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
        if argv_list[:1] == ["gh"]:
            if argv_list[1:3] == ["pr", "create"]:
                return SubprocessOutcome(returncode=0, stdout="https://github.com/x/y/pull/123\n", stderr="")
            return SubprocessOutcome(returncode=0, stdout="", stderr="")
        if argv_list[:2] == ["git", "push"]:
            return SubprocessOutcome(returncode=0, stdout="", stderr="")
        if argv_list[:1] == ["make"]:
            return SubprocessOutcome(returncode=0, stdout="ok\n", stderr="")
        return await real_run(self, argv, cwd=cwd, timeout=timeout, input_text=input_text)

    monkeypatch.setattr(AsyncSubprocessRunner, "run", fake_run)


def git_init_repo(workspace: Path) -> None:
    """Initialize a git repo with a single commit on `main` so phases can stage."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=workspace, check=True)  # nosec B603, B607
    for key, value in (
        ("user.email", "test@example.com"),
        ("user.name", "test-runner"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(  # nosec B603, B607
            ["git", "config", key, value],
            cwd=workspace,
            check=True,
        )
    subprocess.run(  # nosec B603, B607
        ["git", "commit", "--allow-empty", "-q", "-m", "init"],
        cwd=workspace,
        check=True,
    )


def minimal_valid_yaml(template_path: str = "/opt/agent-code/templates/python") -> str:
    """Return a minimal config.yaml string matching the schema."""
    body_phases = ""
    for phase in (
        "classification",
        "dor",
        "comprehension",
        "planning",
        "e2e_writing",
        "implementation",
        "review",
        "summarizer",
    ):
        body_phases += f"  {phase}:\n    url: http://localhost:8000/v1\n    model_name: m\n"
    return (
        "phases:\n"
        + body_phases
        + f"template_path: {template_path}\n"
        + "mcp:\n  context7:\n    url: http://c:1\n  duckduckgo:\n    url: http://d:1\n"
    )
