# Anti-cheat Wrapper

> The single enforcement point that prevents the agent from gaming its own
> success criteria during the implementation phase. Wraps the `ToolRegistry`
> so that every call goes through one place, and rejects writes targeting
> the locked end-to-end test files.

## Why this exists

The most common failure mode of an LLM agent that runs `make test` in a loop is to "fix" failing tests by modifying the tests themselves rather than the production code. This project's spec calls that out as a non-negotiable: the E2E tests committed in the dedicated E2E_WRITING phase are read-only during IMPLEMENTATION. The reviewer agent and the PR gate rely on this invariant.

The wrapper is the only point that enforces it, by inspection of the call arguments before dispatch. There is no allowed escape hatch; bypasses (e.g., via a future `run_shell` tool) must be added to the wrapper as new exclusions, not by working around it.

## What is locked, what is allowed

`is_test_locked_path(path)` returns True if and only if the path matches:

| Path | Locked? | Reason |
|---|---|---|
| `tests/test_foo.py` | yes | E2E test file |
| `tests/sub/test_bar.py` | yes | E2E test in subdir |
| `tests/functional/test_api.py` | yes | functional test |
| `tests/conftest.py` | no | shared fixtures, allowed |
| `tests/sub/conftest.py` | no | per-subdir fixtures |
| `tests/__init__.py` | no | package marker |
| `tests/testdata/sample.json` | no | golden fixture |
| `tests/testdata/test_input.py` | no | testdata always allowed |
| `src/test_x.py` | no | not under tests/ |

The check is purely on the path; the wrapper never reads file content.

## What tools are intercepted

| Tool | Param checked | Action on lock |
|---|---|---|
| `write_file` | `path` | block |
| `edit_file` | `path` | block |
| `delete_file` | `path` | block |
| `move_file` | `src` AND `dst` | block (renaming away from a locked file is also a write) |

Read tools (`read_file`, `list_dir`, `grep`, `find`, `ast_grep`, every `git_*` read tool) are never blocked. `make`, `git_commit`, `git_add`, `git_reset` and the like are not path-targeted writes; they are not intercepted.

## Phase awareness

The wrapper holds a `_phase: PhaseName | None`. The orchestrator calls `set_phase(name)` before invoking each phase, and `set_phase(None)` after the run completes. The lock applies ONLY when `_phase == PhaseName.IMPLEMENTATION`. Other phases (including `E2E_WRITING`, which legitimately writes the test files in the first place) see the underlying registry unchanged.

```python
guard = AntiCheatGuard(registry)
guard.set_phase(PhaseName.IMPLEMENTATION)
result = await guard.call("write_file", path="tests/test_foo.py", content="x")
# result.ok is False, result.error is BLOCK_REASON
# tool.calls is empty (the wrapped tool was never invoked)
```

## Audit trail

Every block is recorded as a `BlockedCall` (frozen dataclass) on the guard:

```python
@dataclass(frozen=True)
class BlockedCall:
    timestamp: datetime
    phase: str
    tool_name: str
    paths: tuple[str, ...]
    reason: str  # = BLOCK_REASON
```

`guard.blocked_calls` returns an immutable snapshot. The orchestrator persists these to `.agent_work/<ticket_id>/attempts.log` (via the `AuditTrail` helper that emits JSONL) so a future reviewer or human can inspect every cheat attempt the agent made during the run.

`AuditTrail.to_jsonl()` emits one JSON object per blocked call:

```jsonl
{"timestamp": "2026-05-04T12:00:00+00:00", "phase": "implementation", "tool": "write_file", "paths": ["tests/test_foo.py"], "reason": "tests/ is read-only..."}
```

## ToolResult on block

When the wrapper blocks a call, it returns:

```python
ToolResult(
    ok=False,
    error="tests/ is read-only during implementation phase; modify production code instead. ...",
    metadata={"blocked": True, "tool": <tool_name>, "paths": [<locked path>, ...]},
)
```

The wrapped tool is NOT called. The block reason is delivered to the model verbatim so it can adjust strategy. The `metadata.blocked` flag lets the loop logic recognize blocks (vs. ordinary tool failures) and increment the "tests-write attempts" counter that triggers an approach reset after 3 attempts within 10 iterations (see FR-008 step 4 in the spec; the counter lands with the implementation loop PR).

## What is NOT covered yet

- **`apply_patch`**: the spec mentions unified-diff edits as a future tool. When it lands, the wrapper must parse the diff to extract target file paths and apply the same rule. Tracked as a TODO in `src/tools/anti_cheat.py`.
- **`run_shell`**: not yet wired into the registry. When it is, the wrapper will inspect the command line for tool invocations like `sed -i tests/test_*.py` and reject them. Today, `run_shell` cannot be reached via the registry, so there is no escape hatch.
- **Stagnation detection** (3 blocks in 10 iterations triggers an approach reset): orchestrator-level concern. The wrapper exposes `blocked_calls` so the loop can count them; the actual counter lands with the implementation loop PR.
- **E2E commit SHA verification** (FR-008 / E2E-026 in the spec): a pre-PR check that the SHA of the E2E commit has not been altered. Independent from this wrapper; lives in the PR creation phase.

## Testing

`tests/test_anti_cheat.py` (33 tests):

- 4 parametrized tests on `is_test_locked_path` covering every lock and every allowed exception.
- Pass-through: no phase, planning, e2e_writing.
- Block: write_file/edit_file/delete_file on locked paths during implementation, all parametrized.
- Block on move_file when `src` OR `dst` is locked.
- Allow: conftest.py, testdata/**, src/** during implementation.
- Allow: read-only tools (read_file) regardless.
- BlockedCall accumulation order, set_phase(None) disabling.
- AuditTrail JSONL round-trip, empty trail.
- BlockedCall immutability.
