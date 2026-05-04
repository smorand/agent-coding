"""`make` target runner exposed as an agent tool.

Wraps invocations of `make <target>`. The set of allowed targets is the
project Makefile contract; the wrapper does not validate it (that is the
phase's job) but it captures stdout/stderr and exit code via the runner.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_MAKE = "make"


class MakeTool:
    """Run a single `make <target>` in the workspace."""

    name = "make"
    description = (
        "Run `make <target>` in the workspace; captures stdout/stderr. "
        "ok=True only when make exits 0; non-zero exit returns ok=False with the output."
    )

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_MAKE,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def call(self, target: str) -> ToolResult:
        """Invoke `make target`. Returns ok=True only on exit 0."""
        if not target.strip():
            return ToolResult(ok=False, error="Empty make target rejected")
        outcome = await self._runner.run([self._binary, target], cwd=self._workspace)
        combined = outcome.stdout + outcome.stderr
        if outcome.returncode == 0:
            return ToolResult(ok=True, output=combined, metadata={"target": target})
        return ToolResult(
            ok=False,
            output=combined,
            error=f"make {target} exited {outcome.returncode}",
            metadata={"target": target, "returncode": outcome.returncode},
        )
