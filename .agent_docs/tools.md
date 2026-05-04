# Tool Registry

> Reference for the tool layer phases use to interact with the workspace.
> Every tool implements the `Tool` Protocol (structural typing) and returns a
> `ToolResult`. A `ToolRegistry` collects tools by name; phases dispatch via
> `registry.call(tool_name, **kwargs)`. The registry is the single seam where
> the future anti-cheat wrapper layer (FR-008) can intercept and reject calls
> based on phase, target path, etc.

## Files

- `src/tools/base.py`: `Tool` Protocol, `ToolResult`, `ToolError`, `SubprocessRunner` Protocol, `SubprocessOutcome`.
- `src/tools/runner.py`: `AsyncSubprocessRunner` (default subprocess runner; never uses shell).
- `src/tools/files.py`: `ReadFileTool`, `WriteFileTool`, `EditFileTool`, `ListDirTool`, `DeleteFileTool`, `MoveFileTool`.
- `src/tools/search.py`: `GrepTool` (ripgrep), `FindFilesTool` (Python pathlib glob), `AstGrepTool`.
- `src/tools/git_ops.py`: `GitStatusTool`, `GitDiffTool`, `GitDiffCachedTool`, `GitAddTool`, `GitCommitTool`, `GitLogTool`, `GitBlameTool`, `GitBranchCreateTool`, `GitCheckoutTool`, `GitResetTool`.
- `src/tools/make_runner.py`: `MakeTool`.
- `src/tools/registry.py`: `ToolRegistry`.
- `src/tools/__init__.py`: re-exports the public surface.

## Protocol contract

```python
class Tool(Protocol):
    name: str
    description: str
    async def call(self, **kwargs: Any) -> ToolResult: ...
```

A class satisfies `Tool` structurally if it has the three members above. It does NOT need to inherit from `Tool`. This lets each concrete tool declare a precise `call` signature (e.g., `call(self, path: str) -> ToolResult`) without violating Liskov on the variadic supertype, while the registry still dispatches via `**kwargs`.

## ToolResult

```python
@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    metadata: Mapping[str, Any] = {}
```

`ok=True` is success. `ok=False` is a soft failure (file not found, no matches, ambiguous edit, etc.) which the phase can recover from. For environment problems (binary missing, timeout) the runner raises `ToolError` instead.

## SubprocessRunner injection

Every tool that shells out (`grep`, `ast-grep`, `git_*`, `make`) accepts an optional `runner: SubprocessRunner` constructor argument. Default: `AsyncSubprocessRunner` over `asyncio.create_subprocess_exec` (never `shell=True`). Tests inject a fake runner to exercise the wrappers without requiring `rg`, `ast-grep`, etc., on the test host.

```python
class FakeRunner:
    async def run(self, argv, *, cwd=None, timeout=30.0, input_text=None):
        return SubprocessOutcome(returncode=0, stdout="...", stderr="")
```

## Workspace scoping

File and search tools are constructed with a `workspace: Path`. Every relative path is resolved against the workspace; paths that escape (`../..`) are rejected with a `ToolResult(ok=False, error="...")`. This is a guardrail, not the anti-cheat layer; the latter is a wrapper around the registry that adds per-phase write restrictions.

## Tool reference

### File ops (workspace-scoped)

| Tool | Signature | Notes |
|---|---|---|
| `ReadFileTool` | `call(path: str, start: int|None, end: int|None)` | Lines 1-indexed inclusive. Hard cap at 1MB without slicing. |
| `WriteFileTool` | `call(path: str, content: str)` | Creates parent dirs; atomic via `.tmp` + rename. |
| `EditFileTool` | `call(path: str, old_string: str, new_string: str)` | Single replace; fails on 0 or 2+ matches. |
| `ListDirTool` | `call(path: str = ".")` | Sorted; directories suffixed with `/`. |
| `DeleteFileTool` | `call(path: str)` | Refuses directories. |
| `MoveFileTool` | `call(src: str, dst: str)` | Creates destination parent dirs. |

### Search

| Tool | Signature | Notes |
|---|---|---|
| `GrepTool` | `call(pattern: str, glob: str|None, path: str = ".")` | ripgrep wrapper; capped at 500 matches. Exit 1 = no matches (still ok=True). |
| `FindFilesTool` | `call(name_pattern: str, path: str = ".")` | Pure Python pathlib glob; no external binary. |
| `AstGrepTool` | `call(pattern: str, lang: str, path: str = ".")` | ast-grep wrapper. |

### Git

All git tools accept `runner` and `binary` constructor args. Concrete `call` signatures:

| Tool | Signature |
|---|---|
| `GitStatusTool` | `call()` |
| `GitDiffTool` | `call(path: str|None = None)` |
| `GitDiffCachedTool` | `call()` |
| `GitAddTool` | `call(paths: Sequence[str])` |
| `GitCommitTool` | `call(message: str)` |
| `GitLogTool` | `call(path: str|None, limit: int = 20)` |
| `GitBlameTool` | `call(path: str, line: int)` |
| `GitBranchCreateTool` | `call(name: str)` |
| `GitCheckoutTool` | `call(ref: str)` |
| `GitResetTool` | `call(target: str, mode: str = "mixed")` (mode in {soft, mixed, hard}) |

### Build

| Tool | Signature | Notes |
|---|---|---|
| `MakeTool` | `call(target: str)` | Combines stdout+stderr into output; ok=True only on exit 0. |

## ToolRegistry

```python
registry = ToolRegistry([
    ReadFileTool(workspace),
    WriteFileTool(workspace),
    GitStatusTool(workspace),
    MakeTool(workspace),
    # ...
])

result = await registry.call("read_file", path="src/x.py")
```

Behavior:

- `__init__` rejects duplicate names with `ToolError`.
- `names` returns sorted tool names.
- `get(name)` and `call(tool_name, **kwargs)` raise `KeyError` on unknown names.
- The first parameter of `call` is `tool_name` (NOT `name`) so tools that take a `name=` kwarg (`git_branch_create`) can be invoked via the registry.

## What is NOT in this PR

- LSP client (FR-005, comprehension phase). Pyright LSP wrapper deferred; TBD-2 in the spec is open on the LSP choice.
- MCP clients (Context7 docs, DuckDuckGo web search). Both deferred; the HTTP shape is non-trivial.
- `gh` CLI wrapper for PR operations (FR-012). Today preflight uses `gh auth status` directly via subprocess; a typed wrapper lands with the PR creation phase.
- `apply_patch` for unified-diff edits. The MVP relies on `edit_file` + `write_file`; `apply_patch` is in the design (Appendix B of the spec) but deferred.
- `read_url` web fetch tool.
- The anti-cheat wrapper layer (FR-008). It will sit between phases and the registry.

## Testing

- `tests/test_tools_runner.py` (5 tests): real `AsyncSubprocessRunner` against `python -c` snippets. Covers stdout/stderr capture, non-zero exit, missing binary, timeout, stdin.
- `tests/test_tools_files.py` (16 tests): every file op, including line-range read, multi-match edit rejection, path escape rejection.
- `tests/test_tools_search.py` (8 tests): grep success/no-match/error, find with sorted paths, find escape rejection, ast-grep success/error.
- `tests/test_tools_git.py` (18 tests): every git wrapper with argv assertion + a stderr-failure case.
- `tests/test_tools_make.py` (3 tests): success, failure with returncode metadata, empty target rejection.
- `tests/test_tools_registry.py` (7 tests): collection, lookup, duplicate rejection, dispatch with kwargs, KeyError on unknown.

135 tests total project-wide; coverage 95.09%.
