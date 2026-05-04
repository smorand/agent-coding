"""Tests for the ToolRegistry dispatch surface."""

from __future__ import annotations

from typing import Any

import pytest

from tools.base import Tool, ToolError, ToolResult
from tools.registry import ToolRegistry


class _NamedTool(Tool):
    """Test tool with a configurable name and a recording call."""

    def __init__(self, name: str, return_text: str = "ok") -> None:
        self.name = name
        self.description = f"test tool {name}"
        self._return_text = return_text
        self.calls: list[dict[str, Any]] = []

    async def call(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(ok=True, output=self._return_text)


def test_registry_collects_by_name() -> None:
    """`names` returns sorted tool names."""
    registry = ToolRegistry([_NamedTool("b"), _NamedTool("a"), _NamedTool("c")])
    assert registry.names == ("a", "b", "c")


def test_registry_get_returns_tool() -> None:
    """`get` returns the registered tool."""
    tool = _NamedTool("x")
    registry = ToolRegistry([tool])
    assert registry.get("x") is tool


def test_registry_has_reflects_membership() -> None:
    """`has` is True for registered names, False otherwise."""
    registry = ToolRegistry([_NamedTool("x")])
    assert registry.has("x") is True
    assert registry.has("y") is False


def test_registry_get_unknown_raises_key_error() -> None:
    """Unknown name raises KeyError."""
    registry = ToolRegistry([_NamedTool("x")])
    with pytest.raises(KeyError):
        registry.get("y")


def test_registry_rejects_duplicate_names() -> None:
    """Two tools with the same name raise ToolError at construction."""
    with pytest.raises(ToolError, match="registered twice"):
        ToolRegistry([_NamedTool("dup"), _NamedTool("dup")])


async def test_registry_call_dispatches_with_kwargs() -> None:
    """`call(name, **kwargs)` reaches the tool with the kwargs intact."""
    tool = _NamedTool("greet", return_text="hi")
    registry = ToolRegistry([tool])
    result = await registry.call("greet", name="Alice", count=3)
    assert result.ok is True
    assert result.output == "hi"
    assert tool.calls == [{"name": "Alice", "count": 3}]


async def test_registry_call_unknown_raises_key_error() -> None:
    """Calling an unknown tool raises KeyError, not a ToolResult."""
    registry = ToolRegistry([_NamedTool("x")])
    with pytest.raises(KeyError):
        await registry.call("y")
