"""Tool registry contract and shared value objects.

Every tool a phase can call satisfies the `Tool` Protocol (it has `name`,
`description`, and an async `call`). Protocols are used over ABCs so that
each concrete tool can declare a precise `call` signature without violating
Liskov on a variadic supertype. A shared `SubprocessRunner` abstraction lets
tests inject a fake runner so they do not need the real binaries (rg,
ast-grep, git, make) to be installed on the test machine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ToolResult:
    """Outcome of a tool invocation.

    `ok=True` means the tool completed successfully. `output` carries the
    primary result (text for read tools, summary for write tools, structured
    payload for searches). `error` is empty on success.
    """

    ok: bool
    output: str = ""
    error: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Raised when a tool encounters an unrecoverable problem.

    Distinguished from a `ToolResult(ok=False)` which represents a recoverable
    or expected failure (e.g., no matches, file not found in read context).
    A ToolError is for setup or environment problems (missing binary, invalid
    arguments).
    """


@dataclass(frozen=True)
class SubprocessOutcome:
    """Captured result of a subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner(Protocol):
    """Async subprocess runner. Default impl uses asyncio.create_subprocess_exec.

    Tests inject a fake runner that returns canned outcomes per command.
    """

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        """Execute `argv`, return its captured outcome. NEVER use shell=True."""
        ...


@runtime_checkable
class Tool(Protocol):
    """Structural type for every tool exposed to phases.

    A tool is any object exposing `name` (str), `description` (str), and an
    async `call(**kwargs)` returning a `ToolResult`. Implementations declare
    precise `call` signatures (e.g., `call(self, path: str) -> ToolResult`);
    the registry invokes them dynamically via `**kwargs`.
    """

    name: str
    description: str

    async def call(self, **kwargs: Any) -> ToolResult:
        """Execute the tool. Implementations declare concrete keyword args."""
        ...
