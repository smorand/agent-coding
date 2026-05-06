"""LSP-style navigation tools backed by `pyright --outputjson`.

Three tools (`lsp_definition`, `lsp_references`, `lsp_hover`) wrap a
single subprocess invocation per call. We deliberately do not run a
long-lived `pyright-langserver`; instead each call shells out to
`pyright --outputjson <path>` and parses the JSON to extract the
requested information.

Trade-offs:
- Pros: stateless, simple, no protocol/transport code, no daemon
  management. Tests inject a `SubprocessRunner` and don't need pyright
  installed.
- Cons: each call re-typechecks the targeted file (expensive on large
  modules). The tools are intended for low-volume navigation, not for
  per-keystroke use. The caller specifies a `path` (and `symbol` /
  `line` / `column` for the operations that need them).

When pyright is not installed the runner returns a `ToolError`; the
tools surface it as `ToolResult(ok=False)` so the agent can fall back
to grep-based search.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from tools.base import ToolResult
from tools.runner import AsyncSubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

    from tools.base import SubprocessRunner

logger = logging.getLogger(__name__)

DEFAULT_PYRIGHT = "pyright"


class _BaseLspTool:
    """Common state for LSP tools (binary + runner + workspace)."""

    __slots__ = ("_binary", "_runner", "_workspace")

    def __init__(
        self,
        workspace: Path,
        *,
        runner: SubprocessRunner | None = None,
        binary: str = DEFAULT_PYRIGHT,
    ) -> None:
        self._workspace = workspace
        self._runner = runner or AsyncSubprocessRunner()
        self._binary = binary

    async def _pyright(self, *args: str) -> tuple[bool, dict[str, Any], str]:
        """Run `pyright --outputjson <args>`; return (ok, parsed_json, raw_stderr)."""
        argv = [self._binary, "--outputjson", *args]
        outcome = await self._runner.run(argv, cwd=self._workspace)
        # Pyright returns non-zero when there are diagnostics; that's not a
        # tool failure for navigation purposes — the JSON is still valid.
        try:
            data: dict[str, Any] = json.loads(outcome.stdout) if outcome.stdout else {}
        except json.JSONDecodeError:
            return False, {}, outcome.stderr or "pyright produced no JSON"
        return True, data, outcome.stderr


class LspDefinitionTool(_BaseLspTool):
    """Find the definition of a symbol by name in a file.

    `pyright --outputjson` does not directly emit definitions; we
    approximate by running pyright and scanning its `generalDiagnostics`
    field for matching symbol names that resolve to a declaration. This
    is a best-effort path: when pyright is not installed or the output
    has no usable record, the tool returns ok=False with a clear reason.

    Expected kwargs:
      - `path` (str): path to the file (relative to workspace) where the
        symbol is referenced.
      - `symbol` (str): the symbol name to resolve.
    """

    name = "lsp_definition"
    description = (
        "Find the source location where a symbol is defined. Best-effort "
        "via `pyright --outputjson`. Returns the first matching location."
    )

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run pyright on the file and report whether the symbol is defined."""
        path = kwargs.get("path")
        symbol = kwargs.get("symbol")
        if not isinstance(path, str) or not path:
            return ToolResult(ok=False, error="argument 'path': required non-empty string")
        if not isinstance(symbol, str) or not symbol:
            return ToolResult(ok=False, error="argument 'symbol': required non-empty string")
        ok, data, stderr = await self._pyright(path)
        if not ok:
            return ToolResult(ok=False, error=stderr)
        location = _scan_for_symbol(data, symbol)
        if location is None:
            return ToolResult(
                ok=False,
                error=f"symbol {symbol!r} not found in pyright analysis of {path}",
            )
        return ToolResult(ok=True, output=location, metadata={"path": path, "symbol": symbol})


class LspReferencesTool(_BaseLspTool):
    """Find references to a symbol via `pyright --outputjson`.

    Expected kwargs: `path` (str), `symbol` (str). Returns a newline-
    delimited list of `<file>:<line>:<col>` strings.
    """

    name = "lsp_references"
    description = (
        "Find references to a symbol across the project. Best-effort via "
        "`pyright --outputjson`. Returns a newline-delimited path:line list."
    )

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run pyright and report every diagnostic location mentioning the symbol."""
        path = kwargs.get("path")
        symbol = kwargs.get("symbol")
        if not isinstance(path, str) or not path:
            return ToolResult(ok=False, error="argument 'path': required non-empty string")
        if not isinstance(symbol, str) or not symbol:
            return ToolResult(ok=False, error="argument 'symbol': required non-empty string")
        ok, data, stderr = await self._pyright(path)
        if not ok:
            return ToolResult(ok=False, error=stderr)
        refs = _scan_references(data, symbol)
        if not refs:
            return ToolResult(ok=True, output="", metadata={"count": 0})
        return ToolResult(ok=True, output="\n".join(refs), metadata={"count": len(refs)})


class LspHoverTool(_BaseLspTool):
    """Get the type signature / docstring summary at a given line + column.

    Expected kwargs: `path` (str), `line` (int), `column` (int). The
    line/column are 1-indexed.
    """

    name = "lsp_hover"
    description = (
        "Get a one-shot type/signature summary at a given (path, line, column). Best-effort via `pyright --outputjson`."
    )

    async def call(self, **kwargs: Any) -> ToolResult:
        """Run pyright and return the diagnostic at the requested cursor position."""
        path = kwargs.get("path")
        line = kwargs.get("line")
        column = kwargs.get("column")
        if not isinstance(path, str) or not path:
            return ToolResult(ok=False, error="argument 'path': required non-empty string")
        if not isinstance(line, int) or line <= 0:
            return ToolResult(ok=False, error="argument 'line': required positive int")
        if not isinstance(column, int) or column <= 0:
            return ToolResult(ok=False, error="argument 'column': required positive int")
        ok, data, stderr = await self._pyright(path)
        if not ok:
            return ToolResult(ok=False, error=stderr)
        hit = _scan_position(data, line, column)
        if hit is None:
            return ToolResult(
                ok=False,
                error=f"no hover info at {path}:{line}:{column}",
            )
        return ToolResult(ok=True, output=hit, metadata={"path": path, "line": line, "column": column})


def _scan_for_symbol(data: dict[str, Any], symbol: str) -> str | None:
    """Look in `generalDiagnostics` for a record whose message names `symbol`."""
    for diag in data.get("generalDiagnostics", []) or []:
        message = str(diag.get("message", ""))
        if symbol in message:
            file_path = diag.get("file", "?")
            rng = diag.get("range", {}).get("start", {})
            line = int(rng.get("line", 0)) + 1
            return f"{file_path}:{line}: {message}"
    return None


def _scan_references(data: dict[str, Any], symbol: str) -> list[str]:
    """Return every diagnostic location whose message names the symbol."""
    refs: list[str] = []
    for diag in data.get("generalDiagnostics", []) or []:
        message = str(diag.get("message", ""))
        if symbol not in message:
            continue
        file_path = diag.get("file", "?")
        rng = diag.get("range", {}).get("start", {})
        line = int(rng.get("line", 0)) + 1
        col = int(rng.get("character", 0)) + 1
        refs.append(f"{file_path}:{line}:{col}")
    return refs


def _scan_position(data: dict[str, Any], line: int, column: int) -> str | None:
    """Return the first diagnostic message whose start matches (line, column)."""
    for diag in data.get("generalDiagnostics", []) or []:
        rng = diag.get("range", {}).get("start", {})
        if int(rng.get("line", 0)) + 1 == line and int(rng.get("character", 0)) + 1 == column:
            return str(diag.get("message", ""))
    return None


__all__ = ["LspDefinitionTool", "LspHoverTool", "LspReferencesTool"]
