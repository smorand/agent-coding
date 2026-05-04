"""Anti-cheat wrapper around the tool registry.

During the IMPLEMENTATION phase, the agent must NOT modify the end-to-end
test files it committed in the dedicated E2E_WRITING phase. This wrapper
intercepts every call that targets a write-class tool and rejects writes
to `tests/test_*.py` (with a few intentional exceptions: `tests/conftest.py`
and `tests/testdata/**` remain writable).

The wrapper is the only enforcement point. Non-implementation phases see
the underlying registry unchanged.

Design notes:

- The check operates on the `path` (and `dst` for moves) keyword arguments
  passed to write-class tools. It is layered on top of the registry so it
  cannot be bypassed by going through `registry.call(...)`.
- Bypassing via `run_shell` (e.g., `sed -i`) is out of scope here because
  `run_shell` is not yet wired into the registry. When it lands, the
  wrapper will be extended to inspect its commands.
- A future extension will inspect `apply_patch` payloads (parse the diff
  to extract target files); see TBD list at the end of this docstring.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from state import PhaseName
from tools.base import ToolResult

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

WRITE_TOOLS_WITH_PATH: frozenset[str] = frozenset({"write_file", "edit_file", "delete_file"})
WRITE_TOOLS_WITH_SRC_DST: frozenset[str] = frozenset({"move_file"})
WRITE_TOOL_NAMES: frozenset[str] = WRITE_TOOLS_WITH_PATH | WRITE_TOOLS_WITH_SRC_DST

BLOCK_REASON = (
    "tests/ is read-only during implementation phase; modify production code instead. "
    "Allowed exceptions: tests/conftest.py, tests/testdata/**."
)


def is_test_locked_path(path: str) -> bool:
    """Return True if `path` is a locked end-to-end test file.

    Locked: `tests/test_*.py` at any depth EXCEPT under `tests/testdata/`.
    Always allowed: any `conftest.py`, any path under `tests/testdata/`.
    """
    if not path:
        return False
    pure = PurePosixPath(path.replace("\\", "/"))
    parts = pure.parts
    if not parts or parts[0] != "tests":
        return False
    # Slice avoids an explicit length check (and a magic-value lint warning).
    if parts[1:2] == ("testdata",):
        return False
    if pure.name == "conftest.py":
        return False
    return pure.name.startswith("test_") and pure.name.endswith(".py")


@dataclass(frozen=True)
class BlockedCall:
    """Record of a single blocked tool call (for audit trail)."""

    timestamp: datetime
    phase: str
    tool_name: str
    paths: tuple[str, ...]
    reason: str = BLOCK_REASON


class AntiCheatGuard:
    """Wrap a `ToolRegistry` to enforce the implementation-phase write lock.

    The orchestrator constructs one guard, then calls `set_phase(name)` before
    handing the guard to each phase. Phases call the guard exactly as they
    would call a `ToolRegistry`. When the active phase is IMPLEMENTATION and
    a call would touch a locked path, the guard returns a failed `ToolResult`
    instead of dispatching, and records a `BlockedCall` for the audit trail.
    """

    __slots__ = ("_blocked", "_phase", "_registry")

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        self._phase: PhaseName | None = None
        self._blocked: list[BlockedCall] = []

    @property
    def names(self) -> tuple[str, ...]:
        """Sorted names of registered tools (delegates to underlying registry)."""
        return self._registry.names

    @property
    def blocked_calls(self) -> tuple[BlockedCall, ...]:
        """Immutable snapshot of all blocks recorded so far."""
        return tuple(self._blocked)

    def set_phase(self, phase: PhaseName | None) -> None:
        """Update the active phase. Pass None to disable enforcement."""
        self._phase = phase

    async def call(self, tool_name: str, **kwargs: Any) -> ToolResult:
        """Dispatch to the tool, blocking writes to locked test paths."""
        if self._phase == PhaseName.IMPLEMENTATION and tool_name in WRITE_TOOL_NAMES:
            blocked_paths = self._extract_locked_paths(tool_name, kwargs)
            if blocked_paths:
                phase_value = self._phase.value if self._phase is not None else "unknown"
                self._blocked.append(
                    BlockedCall(
                        timestamp=datetime.now(UTC),
                        phase=phase_value,
                        tool_name=tool_name,
                        paths=blocked_paths,
                    )
                )
                logger.info(
                    "Blocked %s on %s during phase %s",
                    tool_name,
                    blocked_paths,
                    phase_value,
                )
                return ToolResult(
                    ok=False,
                    error=BLOCK_REASON,
                    metadata={"blocked": True, "tool": tool_name, "paths": list(blocked_paths)},
                )
        return await self._registry.call(tool_name, **kwargs)

    @staticmethod
    def _extract_locked_paths(tool_name: str, kwargs: dict[str, Any]) -> tuple[str, ...]:
        candidates: list[str] = []
        if tool_name in WRITE_TOOLS_WITH_PATH:
            path = kwargs.get("path")
            if isinstance(path, str):
                candidates.append(path)
        elif tool_name in WRITE_TOOLS_WITH_SRC_DST:
            src = kwargs.get("src")
            dst = kwargs.get("dst")
            if isinstance(src, str):
                candidates.append(src)
            if isinstance(dst, str):
                candidates.append(dst)
        return tuple(p for p in candidates if is_test_locked_path(p))


@dataclass
class AuditTrail:
    """Aggregated record of blocked calls (for persistence into .agent_work/)."""

    blocked_calls: list[BlockedCall] = field(default_factory=list)

    def extend(self, calls: tuple[BlockedCall, ...]) -> None:
        """Append `calls` to the trail."""
        self.blocked_calls.extend(calls)

    def to_jsonl(self) -> str:
        """Render the audit trail as JSONL (one event per line)."""
        lines: list[str] = []
        for call in self.blocked_calls:
            lines.append(
                json.dumps(
                    {
                        "timestamp": call.timestamp.isoformat(),
                        "phase": call.phase,
                        "tool": call.tool_name,
                        "paths": list(call.paths),
                        "reason": call.reason,
                    }
                )
            )
        return "\n".join(lines) + ("\n" if lines else "")
