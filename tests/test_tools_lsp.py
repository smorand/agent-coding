"""Tests for the pyright-backed LSP tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tools.base import SubprocessOutcome
from tools.lsp import LspDefinitionTool, LspHoverTool, LspReferencesTool

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class FakeRunner:
    """Returns a canned outcome regardless of argv."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._rc = returncode
        self._stdout = stdout
        self._stderr = stderr

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
        return SubprocessOutcome(returncode=self._rc, stdout=self._stdout, stderr=self._stderr)


def _pyright_payload_with(message: str, file: str = "src/foo.py", line: int = 9, col: int = 4) -> str:
    return json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": file,
                    "message": message,
                    "range": {"start": {"line": line, "character": col}},
                }
            ]
        }
    )


# ──────────────────────────────────────────────────────────────────────────────
# LspDefinitionTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_definition_returns_match_when_symbol_appears(tmp_path: Path) -> None:
    """Pyright output containing the symbol name yields a path:line summary."""
    runner = FakeRunner(stdout=_pyright_payload_with("'subtract' is undefined"))
    tool = LspDefinitionTool(tmp_path, runner=runner)

    result = await tool.call(path="src/foo.py", symbol="subtract")

    assert result.ok
    assert "src/foo.py" in result.output
    assert "10" in result.output  # line 9 (0-indexed) -> 10 (1-indexed)


async def test_definition_returns_not_ok_when_symbol_absent(tmp_path: Path) -> None:
    """No matching diagnostic produces ok=False."""
    runner = FakeRunner(stdout=_pyright_payload_with("'foo' is undefined"))
    tool = LspDefinitionTool(tmp_path, runner=runner)

    result = await tool.call(path="src/foo.py", symbol="bar")

    assert not result.ok
    assert "not found" in result.error


async def test_definition_validates_arguments(tmp_path: Path) -> None:
    """Missing or empty `path` / `symbol` are rejected before invoking pyright."""
    runner = FakeRunner()
    tool = LspDefinitionTool(tmp_path, runner=runner)

    r1 = await tool.call(symbol="x")
    r2 = await tool.call(path="src/foo.py")

    assert not r1.ok
    assert not r2.ok
    assert runner.calls == []


async def test_definition_handles_non_json_output(tmp_path: Path) -> None:
    """Non-JSON pyright output yields ok=False with the stderr message."""
    runner = FakeRunner(stdout="not json", stderr="pyright: command not found")
    tool = LspDefinitionTool(tmp_path, runner=runner)

    result = await tool.call(path="src/foo.py", symbol="x")

    assert not result.ok
    assert "command not found" in result.error or "no JSON" in result.error


# ──────────────────────────────────────────────────────────────────────────────
# LspReferencesTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_references_lists_each_match(tmp_path: Path) -> None:
    """Multiple matching diagnostics produce a newline-separated path:line:col list."""
    payload = json.dumps(
        {
            "generalDiagnostics": [
                {
                    "file": "src/a.py",
                    "message": "reference to 'x'",
                    "range": {"start": {"line": 0, "character": 0}},
                },
                {
                    "file": "src/b.py",
                    "message": "another use of 'x'",
                    "range": {"start": {"line": 4, "character": 7}},
                },
            ]
        }
    )
    runner = FakeRunner(stdout=payload)
    tool = LspReferencesTool(tmp_path, runner=runner)

    result = await tool.call(path="src/a.py", symbol="'x'")

    assert result.ok
    assert "src/a.py:1:1" in result.output
    assert "src/b.py:5:8" in result.output
    assert result.metadata["count"] == 2


async def test_references_returns_zero_count_for_no_match(tmp_path: Path) -> None:
    """No matching diagnostic returns ok=True with count=0."""
    runner = FakeRunner(stdout=json.dumps({"generalDiagnostics": []}))
    tool = LspReferencesTool(tmp_path, runner=runner)

    result = await tool.call(path="src/a.py", symbol="missing")

    assert result.ok
    assert result.output == ""
    assert result.metadata["count"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# LspHoverTool
# ──────────────────────────────────────────────────────────────────────────────


async def test_hover_returns_message_at_position(tmp_path: Path) -> None:
    """A diagnostic at exactly the requested (line, col) is returned."""
    runner = FakeRunner(stdout=_pyright_payload_with("expected str", line=4, col=9))
    tool = LspHoverTool(tmp_path, runner=runner)

    result = await tool.call(path="src/foo.py", line=5, column=10)  # 1-indexed

    assert result.ok
    assert "expected str" in result.output


async def test_hover_returns_not_ok_when_no_diagnostic(tmp_path: Path) -> None:
    """A position with no diagnostic yields ok=False."""
    runner = FakeRunner(stdout=_pyright_payload_with("at a different spot", line=99, col=99))
    tool = LspHoverTool(tmp_path, runner=runner)

    result = await tool.call(path="src/foo.py", line=1, column=1)

    assert not result.ok


async def test_hover_rejects_invalid_arguments(tmp_path: Path) -> None:
    """Non-positive line/column or missing path is rejected without invoking pyright."""
    runner = FakeRunner()
    tool = LspHoverTool(tmp_path, runner=runner)

    r1 = await tool.call(path="x", line=0, column=1)
    r2 = await tool.call(path="x", line=1, column=0)
    r3 = await tool.call(line=1, column=1)

    assert not r1.ok
    assert not r2.ok
    assert not r3.ok
    assert runner.calls == []
