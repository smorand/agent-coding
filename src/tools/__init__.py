"""Tool registry for agent-code.

Each tool implements `tools.base.Tool` and returns a `ToolResult`. Tools are
collected into a `ToolRegistry` per phase; future anti-cheat wrappers will
intercept calls at the registry layer (FR-008 implementation lock).

Public surface:

- File ops: `ReadFileTool`, `WriteFileTool`, `EditFileTool`, `ListDirTool`,
  `DeleteFileTool`, `MoveFileTool`.
- Search: `GrepTool` (ripgrep), `FindFilesTool` (Python glob), `AstGrepTool`.
- Git: status, diff, diff-cached, add, commit, log, blame, branch-create,
  checkout, reset.
- Build: `MakeTool`.
- Runtime: `AsyncSubprocessRunner` (default), `SubprocessRunner` protocol
  for test injection.
- Registry: `ToolRegistry`, `ToolResult`, `ToolError`.
"""

from __future__ import annotations

from tools.anti_cheat import (
    BLOCK_REASON,
    WRITE_TOOL_NAMES,
    AntiCheatGuard,
    AuditTrail,
    BlockedCall,
    is_test_locked_path,
)
from tools.base import (
    DEFAULT_SUBPROCESS_TIMEOUT_SECONDS,
    SubprocessOutcome,
    SubprocessRunner,
    Tool,
    ToolError,
    ToolResult,
)
from tools.files import (
    DeleteFileTool,
    EditFileTool,
    ListDirTool,
    MoveFileTool,
    ReadFileTool,
    WriteFileTool,
)
from tools.git_ops import (
    GitAddTool,
    GitBlameTool,
    GitBranchCreateTool,
    GitCheckoutTool,
    GitCommitTool,
    GitDiffCachedTool,
    GitDiffTool,
    GitLogTool,
    GitResetTool,
    GitStatusTool,
)
from tools.make_runner import MakeTool
from tools.registry import ToolRegistry
from tools.runner import AsyncSubprocessRunner
from tools.search import AstGrepTool, FindFilesTool, GrepTool

__all__ = [
    "BLOCK_REASON",
    "DEFAULT_SUBPROCESS_TIMEOUT_SECONDS",
    "WRITE_TOOL_NAMES",
    "AntiCheatGuard",
    "AstGrepTool",
    "AsyncSubprocessRunner",
    "AuditTrail",
    "BlockedCall",
    "DeleteFileTool",
    "EditFileTool",
    "FindFilesTool",
    "GitAddTool",
    "GitBlameTool",
    "GitBranchCreateTool",
    "GitCheckoutTool",
    "GitCommitTool",
    "GitDiffCachedTool",
    "GitDiffTool",
    "GitLogTool",
    "GitResetTool",
    "GitStatusTool",
    "GrepTool",
    "ListDirTool",
    "MakeTool",
    "MoveFileTool",
    "ReadFileTool",
    "SubprocessOutcome",
    "SubprocessRunner",
    "Tool",
    "ToolError",
    "ToolRegistry",
    "ToolResult",
    "WriteFileTool",
    "is_test_locked_path",
]
