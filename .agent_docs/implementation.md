# Implementation loop phase (FR-008 / partial FR-010)

> Single-approach iterative loop. LLM produces source-file edits as
> `## FILE:` blocks; the agent writes them, runs `make check`, feeds
> the output back. Loops until convergence or one of the bail-out
> conditions fires. On convergence, commits the diff with the canonical
> message.

## Files

- `src/phases/implementation.py`:
  - `ImplementationPhase(*, llm_client=None, workspace=None, runner=None,
    max_iterations=10, stagnation_threshold=5)`. The MVP default of 10
    iterations is below the spec's 30 to control LLM cost; the spec
    default applies in production via configuration.
  - `EditedFile(path, content)`, `IterationOutcome(...)`,
    `ImplementationReport(...)`: persisted dataclasses.
  - `parse_implementation_response`, `validate_implementation_path`:
    pure helpers exported for tests.

## LLM contract

The model must reply with one or more blocks of:

```
## FILE: <relative path>
```python
<full file content>
```
```

Only blocks; no other text. Any path that matches `is_test_locked_path`
(i.e. `tests/test_*.py`) is rejected; `tests/conftest.py` and
`tests/testdata/**` remain writable.

## Loop

For each iteration up to `max_iterations`:

1. Build user prompt: ticket + plan + current `git diff` + last 5
   iterations' tails (failing signature, stdout/stderr tails, parse
   errors).
2. LLM call. `LlmError` → record iteration with `parse_error`, halt
   with `HALT_EXHAUSTED`.
3. Parse response. Parse failure → record iteration with `parse_error`,
   continue to next iteration so the model can self-correct.
4. Apply edits via direct `pathlib` writes (paths already validated).
5. Run `make check` via `MakeTool`.
6. Compute the failing-test signature from `^FAILED <path>::<name>` lines.
7. Record `IterationOutcome(returncode, stdout/stderr tail, signature, …)`.
8. If returncode == 0:
   - `git add` the edited files, `git commit -m "Implement <ticket-id>"`,
     capture HEAD SHA → `state.implementation_commit_sha`.
   - Persist `implementation.json`. Return CONTINUE.
9. Stagnation check: if signature is identical for `stagnation_threshold`
   iterations in a row, halt with `HALT_EXHAUSTED` and the message
   `"loop stagnated: same failing tests for N iterations …"`.

After the loop exits without convergence: persist `implementation.json`
with `final_status="exhausted"`, return `HALT_EXHAUSTED`.

## Persisted artifact

`.agent_work/<ticket-id>/implementation.json`:
```json
{
  "final_status": "converged",
  "commit_sha": "…",
  "model": "qwen3-32b",
  "total_input_tokens": 1234,
  "total_output_tokens": 567,
  "generated_at": "…",
  "iterations": [
    {
      "iteration": 1,
      "files_edited": ["src/calc.py"],
      "check_returncode": 0,
      "check_stdout": "…",
      "check_stderr": "",
      "failing_signature": "",
      "parse_error": ""
    }
  ]
}
```

## What is NOT done by this PR

- **FR-009 multi-approach exploration**: when one approach fails, the
  spec mandates a `git reset --hard <e2e_commit>` + a fresh approach,
  with `min_approaches=3` total. The MVP exits after a single approach.
- **FR-010 rolling-summary compression**: the prompt keeps the last 5
  iterations verbatim, capped per-iteration at 2 KB. The summarizer
  model is wired in config but unused.
- **Regression detection**: more failures than the previous successful
  state should trigger a `git reset` to that state. Not implemented.
- **Wall-clock budget**: spec default 2h. Not implemented.

## Anti-cheat layering

- Parser-layer validation rejects locked paths (defense in depth).
- The orchestrator's `AntiCheatGuard` enforces the same rule for any
  registry-routed `write_file` / `edit_file` etc. The implementation
  phase does NOT route through the registry today (it writes via
  `pathlib`), so the parser check is the only enforcement point.
- A future enhancement will route writes through the guard so that
  any future tool-using phase inherits the same lock.
