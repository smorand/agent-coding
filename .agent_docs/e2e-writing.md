# E2E writing phase (FR-007)

> Generates pytest test files from the ticket's acceptance criteria and
> the persisted plan, writes them under `tests/`, and commits them with
> the message `Add E2E tests for <ticket-id>`. The commit SHA becomes the
> "lock" the implementation phase must respect (E2E-026).

## Files

- `src/phases/e2e_writing.py`:
  - `E2eWritingPhase(*, llm_client=None, workspace=None, git_runner=None)`.
    Without an LLM client the phase logs and returns CONTINUE (skeleton
    fallback). The `git_runner` parameter accepts a `SubprocessRunner`
    test double; the production default is `AsyncSubprocessRunner`.
  - `E2eFile(path, content)`: one parsed test file.
  - `E2eWritingReport(files, commit_sha, model, input_tokens, output_tokens, generated_at)`:
    persisted metadata.
  - Pure helpers exported for unit tests: `parse_e2e_response`,
    `validate_test_path`.
- `src/state.py`: adds `State.e2e_commit_sha: str | None` so the
  implementation phase (and a future PR-creation gate) can detect
  tampering.

## Persisted artifacts

- The committed test files themselves (`tests/test_*.py` …).
- `.agent_work/<ticket-id>/e2e_writing.json`:
  ```json
  {
    "commit_sha": "abc123…",
    "model": "qwen3-32b",
    "input_tokens": 200,
    "output_tokens": 300,
    "generated_at": "2026-05-05T07:32:54+00:00",
    "files": [
      {"path": "tests/test_subtract.py", "size_bytes": 412}
    ]
  }
  ```

## Prompt

The system prompt fixes the output format strictly:

```
## FILE: tests/test_<name>.py
```python
<file content>
```
```

`parse_e2e_response` runs the regex
`^##\s*FILE:\s*(\S+)\n```(python|py)?\n(...)\n```` against the response.
Each match is validated against `validate_test_path`:

- Path must start with `tests/`.
- Path must not contain `..` or be absolute.
- Leaf filename must match `test_*.py`.
- No duplicate paths within one response.

Any failure produces `HALT_ERROR` (exit 3). Same for `LlmError` and any
file-system error encountered while reading the inputs.

The user prompt always contains the ticket text. When `plan.md` exists
in the work directory it is appended; the comprehension report is *not*
included (per the spec: no carryover beyond ticket and plan).

## Git commit

After the files are written:

1. `GitAddTool.call(paths=[...])` stages every file.
2. `GitCommitTool.call(message="Add E2E tests for <ticket-id>")` creates
   the commit.
3. `git rev-parse HEAD` reads the resulting SHA.
4. The SHA is recorded both in `e2e_writing.json` and in
   `ctx.state.e2e_commit_sha`.

Both git tools share a single `SubprocessRunner` (the default
`AsyncSubprocessRunner`, or the injected test double).

A failure at any of these steps surfaces `HALT_ERROR` with the underlying
error string; the artifacts on disk are kept (they would be discarded
manually or by the next `git reset`).

## Wiring

- `agent_code._build_pipeline_components` requests
  `llm_factory.for_phase("e2e_writing")` and passes the client to
  `E2eWritingPhase`.
- The CLI does not currently inject a custom git runner; the phase
  spawns its own `AsyncSubprocessRunner`, which inherits the parent
  process's environment so a normally-configured local git works.

## Anti-cheat boundary

`AntiCheatGuard` does NOT block writes during the `E2E_WRITING` phase;
the phase needs free access to `tests/`. Once the orchestrator
transitions to `IMPLEMENTATION`, the guard activates and any future
`write_file` / `edit_file` / `delete_file` / `move_file` targeting
`tests/test_*.py` is rejected with the canonical reason. Files under
`tests/conftest.py` and `tests/testdata/**` remain writable.

## Testing

- `tests/test_phases_e2e_writing.py` (16 tests):
  - Pure helpers: response parsing (single file, multiple files, missing
    block, duplicates, path outside `tests/`, non-`test_*.py` filename),
    path validation (subdirectories, traversal, absolute, empty).
  - Phase: no-LLM noop; full path with `FakeLlmClient` + `FakeRunner`
    (writes file, runs `git add`, `git commit`, `git rev-parse`);
    persisted JSON with metadata; `state.e2e_commit_sha` mutation;
    plan inclusion in user prompt; `HALT_ERROR` on `LlmError`,
    malformed response, and failed `git commit`.
- `tests/test_agent_code.py`: the `stub_llm` fixture now also returns an
  E2E-shaped response when the system prompt looks like the E2E phase;
  the bootstrap E2E test initializes a real git repo via `_git_init`
  helper so the in-pipeline commit succeeds.

## What is NOT done by this PR

- **Test commit verification at PR-creation time** (E2E-026): the SHA
  is recorded; the verification step lands with FR-012.
- **Multi-approach reset**: when the implementation loop exhausts an
  approach, it must `git reset --hard <e2e_commit_sha>`. That logic
  lives in the implementation phase (FR-009).
- **Anti-cheat for E2E_WRITING**: the spec also says E2E_WRITING has no
  access to write tools for files outside `tests/`. The current phase
  only writes what `parse_e2e_response` returned, with explicit path
  validation, so the spec rule is satisfied at the phase level even
  without a corresponding guard mode.
