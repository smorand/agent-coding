"""Tool registry: collects tools by name and dispatches calls.

Phases hold a `ToolRegistry`; they look up tools by name and invoke them.
The registry is the single seam where the future anti-cheat wrapper layer
can intercept calls (e.g., block writes to `tests/test_*.py` during the
implementation phase).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.base import ToolError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from tools.base import Tool, ToolResult


class ToolRegistry:
    """Lookup and dispatch for the set of tools available to a phase."""

    __slots__ = ("_tools",)

    def __init__(self, tools: Iterable[Tool]) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                msg = f"Tool name {tool.name!r} registered twice"
                raise ToolError(msg)
            self._tools[tool.name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        """Sorted tuple of registered tool names."""
        return tuple(sorted(self._tools))

    def get(self, name: str) -> Tool:
        """Return the tool registered under `name`, or raise `KeyError`."""
        return self._tools[name]

    def has(self, name: str) -> bool:
        """True if a tool with `name` is registered."""
        return name in self._tools

    async def call(self, tool_name: str, **kwargs: Any) -> ToolResult:
        """Dispatch to the named tool. Unknown name raises `KeyError`.

        The first parameter is `tool_name` rather than `name` so that tools
        accepting a `name=` keyword argument (e.g., `git_branch_create`) can
        be called via the registry without a collision.
        """
        return await self._tools[tool_name].call(**kwargs)
