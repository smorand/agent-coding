"""Tests for the `gh` CLI wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.base import SubprocessOutcome
from tools.gh import (
    GhIssueCommentTool,
    GhLabelEnsureTool,
    GhPrCommentTool,
    GhPrCreateTool,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class FakeRunner:
    """Records argv passed to it; returns canned outcomes per invocation."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.outcomes: list[SubprocessOutcome] = []

    def queue(self, outcome: SubprocessOutcome) -> None:
        self.outcomes.append(outcome)

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
        if self.outcomes:
            return self.outcomes.pop(0)
        return SubprocessOutcome(returncode=0, stdout="", stderr="")


# ──────────────────────────────────────────────────────────────────────────────
# GhPrCreateTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_pr_create_builds_argv_with_title_and_body(tmp_path: Path) -> None:
    """The tool sends `gh pr create --title <t> --body <b>` to the runner."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=0, stdout="https://github.com/x/y/pull/1\n", stderr=""))
    tool = GhPrCreateTool(tmp_path, runner=runner)

    result = await tool.call(title="My PR", body="some body")

    assert result.ok
    assert "https://github.com/x/y/pull/1" in result.output
    argv = runner.calls[0]
    assert argv[:3] == ["gh", "pr", "create"]
    title_idx = argv.index("--title")
    body_idx = argv.index("--body")
    assert argv[title_idx + 1] == "My PR"
    assert argv[body_idx + 1] == "some body"


async def test_pr_create_appends_draft_flag_when_requested(tmp_path: Path) -> None:
    """The `--draft` flag is added when `draft=True`."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=0, stdout="", stderr=""))
    tool = GhPrCreateTool(tmp_path, runner=runner)

    await tool.call(title="t", body="b", draft=True)

    assert "--draft" in runner.calls[0]


async def test_pr_create_appends_label_for_each_label(tmp_path: Path) -> None:
    """Each label produces its own `--label <name>` pair in argv."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=0, stdout="", stderr=""))
    tool = GhPrCreateTool(tmp_path, runner=runner)

    await tool.call(title="t", body="b", labels=["bug", "high-priority"])

    argv = runner.calls[0]
    label_idxs = [i for i, a in enumerate(argv) if a == "--label"]
    assert len(label_idxs) == 2
    assert argv[label_idxs[0] + 1] == "bug"
    assert argv[label_idxs[1] + 1] == "high-priority"


async def test_pr_create_supports_base_and_head(tmp_path: Path) -> None:
    """`base` and `head` map to the corresponding flags."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=0, stdout="", stderr=""))
    tool = GhPrCreateTool(tmp_path, runner=runner)

    await tool.call(title="t", body="b", base="main", head="feat/x")

    argv = runner.calls[0]
    assert argv[argv.index("--base") + 1] == "main"
    assert argv[argv.index("--head") + 1] == "feat/x"


async def test_pr_create_rejects_missing_title(tmp_path: Path) -> None:
    """An empty title is rejected before invoking the runner."""
    runner = FakeRunner()
    tool = GhPrCreateTool(tmp_path, runner=runner)

    result = await tool.call(title="", body="b")

    assert not result.ok
    assert "title" in result.error
    assert runner.calls == []


async def test_pr_create_rejects_invalid_labels(tmp_path: Path) -> None:
    """A non-list `labels` arg fails before invoking the runner."""
    runner = FakeRunner()
    tool = GhPrCreateTool(tmp_path, runner=runner)

    result = await tool.call(title="t", body="b", labels="bug")  # type: ignore[arg-type]

    assert not result.ok
    assert "labels" in result.error
    assert runner.calls == []


async def test_pr_create_surfaces_gh_failure(tmp_path: Path) -> None:
    """A non-zero exit from `gh` produces ToolResult(ok=False) with stderr."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=1, stdout="", stderr="auth failed"))
    tool = GhPrCreateTool(tmp_path, runner=runner)

    result = await tool.call(title="t", body="b")

    assert not result.ok
    assert "auth failed" in result.error


# ──────────────────────────────────────────────────────────────────────────────
# GhPrCommentTool / GhIssueCommentTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_pr_comment_sends_argv(tmp_path: Path) -> None:
    """`gh pr comment <number> --body <body>` is sent to the runner."""
    runner = FakeRunner()
    tool = GhPrCommentTool(tmp_path, runner=runner)

    await tool.call(pr_number=42, body="LGTM")

    assert runner.calls[0] == ["gh", "pr", "comment", "42", "--body", "LGTM"]


async def test_pr_comment_rejects_missing_args(tmp_path: Path) -> None:
    """Missing `pr_number` or empty body fails fast."""
    runner = FakeRunner()
    tool = GhPrCommentTool(tmp_path, runner=runner)

    result = await tool.call(body="hi")
    assert not result.ok
    result = await tool.call(pr_number=1, body="")
    assert not result.ok
    assert runner.calls == []


async def test_issue_comment_sends_argv(tmp_path: Path) -> None:
    """`gh issue comment <number> --body <body>` is sent."""
    runner = FakeRunner()
    tool = GhIssueCommentTool(tmp_path, runner=runner)

    await tool.call(issue_number=7, body="see PR #42")

    assert runner.calls[0] == ["gh", "issue", "comment", "7", "--body", "see PR #42"]


# ──────────────────────────────────────────────────────────────────────────────
# GhLabelEnsureTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_label_ensure_creates_label_when_absent(tmp_path: Path) -> None:
    """A successful `gh label create` returns ok=True."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=0, stdout="", stderr=""))
    tool = GhLabelEnsureTool(tmp_path, runner=runner)

    result = await tool.call(name="bug", color="d73a4a", description="bug report")

    assert result.ok
    argv = runner.calls[0]
    assert argv[:3] == ["gh", "label", "create"]
    assert "bug" in argv
    assert argv[argv.index("--color") + 1] == "d73a4a"
    assert argv[argv.index("--description") + 1] == "bug report"


async def test_label_ensure_treats_already_exists_as_success(tmp_path: Path) -> None:
    """An `already exists` failure from `gh label create` is mapped to ok=True."""
    runner = FakeRunner()
    runner.queue(
        SubprocessOutcome(
            returncode=1,
            stdout="",
            stderr="HTTP 422: Validation Failed (label already exists)",
        )
    )
    tool = GhLabelEnsureTool(tmp_path, runner=runner)

    result = await tool.call(name="bug")

    assert result.ok
    assert "already exists" in result.output


async def test_label_ensure_propagates_other_failures(tmp_path: Path) -> None:
    """Any other `gh` failure is surfaced as ok=False with the original stderr."""
    runner = FakeRunner()
    runner.queue(SubprocessOutcome(returncode=1, stdout="", stderr="auth failed"))
    tool = GhLabelEnsureTool(tmp_path, runner=runner)

    result = await tool.call(name="bug")

    assert not result.ok
    assert "auth" in result.error


async def test_label_ensure_rejects_missing_name(tmp_path: Path) -> None:
    """An empty label name fails before invoking the runner."""
    runner = FakeRunner()
    tool = GhLabelEnsureTool(tmp_path, runner=runner)

    result = await tool.call(name="")

    assert not result.ok
    assert "name" in result.error
    assert runner.calls == []
