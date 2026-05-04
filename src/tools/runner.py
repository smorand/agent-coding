"""Default `SubprocessRunner` implementation using asyncio.

Wraps `asyncio.create_subprocess_exec` with capture of stdout/stderr, a
hard timeout, and never invokes the shell.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from tools.base import DEFAULT_SUBPROCESS_TIMEOUT_SECONDS, SubprocessOutcome, ToolError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class AsyncSubprocessRunner:
    """Default runner. Stateless; safe to share across tools."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout: float = DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
        input_text: str | None = None,
    ) -> SubprocessOutcome:
        """Execute `argv` and capture stdout/stderr.

        Raises `ToolError` if the process cannot be spawned or exceeds the
        timeout. A non-zero exit code is NOT an error here; callers decide
        based on the returncode.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if input_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd is not None else None,
            )
        except (OSError, FileNotFoundError) as exc:
            msg = f"Cannot spawn {argv[0]!r}: {exc}"
            raise ToolError(msg) from exc
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=input_text.encode("utf-8") if input_text else None),
                timeout=timeout,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            msg = f"Process {argv[0]!r} exceeded timeout of {timeout}s"
            raise ToolError(msg) from exc
        return SubprocessOutcome(
            returncode=process.returncode if process.returncode is not None else -1,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
        )
