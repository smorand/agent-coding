"""GitHub CLI wrappers exposed as agent tools.

Thin wrappers around the `gh` binary via the injectable `SubprocessRunner`.
Each tool builds a typed argv list (no shell, no string interpolation),
matching the style of the git tools. The `gh` binary must be installed
and authenticated on the host (verified by the preflight check).

Tools:
- `gh_pr_create`: open a Pull Request (`gh pr create`).
- `gh_pr_comment`: add a comment to a Pull Request.
- `gh_issue_comment`: add a comment to an issue (used for ticket comments).
- `gh_label_ensure`: idempotently create a repository label.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_GH = "gh"
LABEL_ALREADY_EXISTS_MARKERS: tuple[str, ...] = ("already exists", "HTTP 422")


class _BaseGhTool:
    """Shared state for `gh` tools (binary + runner + workspace).

    Not a `Tool` itself; subclasses satisfy the `Tool` Protocol structurally.
    """

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_GH,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def _gh(self, *args: str) -> ToolResult:
        argv: list[str] = [self._binary, *args]
        outcome = await self._runner.run(argv, cwd=self._workspace)
        if outcome.returncode == 0:
            return ToolResult(ok=True, output=outcome.stdout)
        return ToolResult(
            ok=False,
            output=outcome.stdout,
            error=outcome.stderr or f"gh exited {outcome.returncode}",
        )


class GhPrCreateTool(_BaseGhTool):
    """Open a Pull Request via `gh pr create`.

    Required arguments:
    - `title`: PR title (string).
    - `body`: PR description (string).

    Optional arguments:
    - `draft`: open as draft (bool, default False).
    - `labels`: list[str] of label names to apply.
    - `base`: target branch (default: repo default).
    - `head`: source branch (default: current branch).

    On success, `output` carries the PR URL printed by `gh` on stdout.
    """

    name = "gh_pr_create"
    description = "Open a Pull Request. Returns the PR URL on success."

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run `gh pr create` with the given title/body and optional flags."""
        title = kwargs.get("title")
        body = kwargs.get("body")
        if not isinstance(title, str) or not title:
            return ToolResult(ok=False, error="argument 'title': required non-empty string")
        if not isinstance(body, str) or not body:
            return ToolResult(ok=False, error="argument 'body': required non-empty string")
        args: list[str] = ["pr", "create", "--title", title, "--body", body]
        if bool(kwargs.get("draft", False)):
            args.append("--draft")
        labels = kwargs.get("labels")
        if labels:
            if not isinstance(labels, (list, tuple)) or any(not isinstance(label, str) for label in labels):
                return ToolResult(ok=False, error="argument 'labels': must be a list of strings")
            for label in labels:
                args.extend(["--label", label])
        base = kwargs.get("base")
        if isinstance(base, str) and base:
            args.extend(["--base", base])
        head = kwargs.get("head")
        if isinstance(head, str) and head:
            args.extend(["--head", head])
        return await self._gh(*args)


class GhPrCommentTool(_BaseGhTool):
    """Comment on a Pull Request via `gh pr comment`.

    Required arguments:
    - `pr_number`: PR number (int) or branch reference (str).
    - `body`: comment body (string).
    """

    name = "gh_pr_comment"
    description = "Post a comment on a Pull Request."

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run `gh pr comment <number> --body <body>`."""
        pr_number = kwargs.get("pr_number")
        body = kwargs.get("body")
        if pr_number is None:
            return ToolResult(ok=False, error="argument 'pr_number': required")
        if not isinstance(body, str) or not body:
            return ToolResult(ok=False, error="argument 'body': required non-empty string")
        return await self._gh("pr", "comment", str(pr_number), "--body", body)


class GhIssueCommentTool(_BaseGhTool):
    """Comment on an issue via `gh issue comment` (used for ticket comments).

    Required arguments:
    - `issue_number`: issue number (int) or URL (str).
    - `body`: comment body (string).
    """

    name = "gh_issue_comment"
    description = "Post a comment on an issue."

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run `gh issue comment <number> --body <body>`."""
        issue_number = kwargs.get("issue_number")
        body = kwargs.get("body")
        if issue_number is None:
            return ToolResult(ok=False, error="argument 'issue_number': required")
        if not isinstance(body, str) or not body:
            return ToolResult(ok=False, error="argument 'body': required non-empty string")
        return await self._gh("issue", "comment", str(issue_number), "--body", body)


class GhLabelEnsureTool(_BaseGhTool):
    """Idempotently create a repository label via `gh label create`.

    Required arguments:
    - `name`: label name (string).

    Optional arguments:
    - `color`: hex color without leading `#` (string).
    - `description`: short description (string).

    `already exists` errors are treated as success (the label is now present).
    """

    name = "gh_label_ensure"
    description = "Ensure a repository label exists. No-op if it already does."

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run `gh label create <name> [--color X] [--description Y]`."""
        label_name = kwargs.get("name")
        if not isinstance(label_name, str) or not label_name:
            return ToolResult(ok=False, error="argument 'name': required non-empty string")
        args: list[str] = ["label", "create", label_name]
        color = kwargs.get("color")
        if isinstance(color, str) and color:
            args.extend(["--color", color])
        description = kwargs.get("description")
        if isinstance(description, str) and description:
            args.extend(["--description", description])
        result = await self._gh(*args)
        if result.ok:
            return result
        if _label_already_exists(result):
            return ToolResult(ok=True, output=f"label {label_name!r} already exists")
        return result


def _label_already_exists(result: ToolResult) -> bool:
    """True when `gh label create` failed because the label already exists."""
    haystacks: Sequence[str] = (result.error.lower(), result.output.lower())
    return any(marker.lower() in field for marker in LABEL_ALREADY_EXISTS_MARKERS for field in haystacks)
