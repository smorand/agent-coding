# Bootstrap (FR-014)

> Materializes the canonical Python project template from `vars/project-template/`
> into an empty workspace. Triggered by the classification phase when it
> detects EMPTY and a `template_path` is configured. Pure copy + placeholder
> substitution + entry-file rename; `uv sync` and the initial git commit
> are deferred to follow-up wiring.

## Files

- `src/bootstrap.py`: pure logic. Public surface:
  - `extract_inputs_from_ticket(ticket_path, *, fallback_author, fallback_email, now)` returns a `BootstrapInputs` value object from the ticket frontmatter and the first paragraph of `## Description`.
  - `materialize_template(workspace, template_path, inputs)` walks the template directory, substitutes placeholders in text files, copies binary files byte-for-byte, renames any path containing `__PROJECT_ENTRY__`, and returns a `BootstrapResult` listing every materialized file plus the captured `.template_version`.
  - `BootstrapError`: raised on missing template, missing ticket, missing/invalid frontmatter, missing `id`.
- `src/phases/classification.py`: the integration point. `ClassificationPhase(template_path=Path(...))` calls bootstrap when EMPTY is detected; without `template_path`, EMPTY halts with a clear error.

## Substitution placeholders

| Placeholder | Source |
|---|---|
| `__PROJECT_NAME__` | ticket frontmatter `id` (kebab-case, e.g., `add-subtract`) |
| `__PROJECT_ENTRY__` | snake_case of project name (`add_subtract`) |
| `__PROJECT_DESCRIPTION__` | first non-empty paragraph of `## Description` (whitespace collapsed to single spaces). Fallback: a generic `"Project bootstrapped from the agent-code Python template."`. |
| `__PROJECT_AUTHOR__` | ticket frontmatter `author`, else `fallback_author` (default: "Unknown") |
| `__PROJECT_AUTHOR_EMAIL__` | not in the ticket; supplied by the caller (`fallback_email`, default: `unknown@example.com`) |
| `__PROJECT_YEAR__` | year from `now` (defaults to `datetime.now(UTC)`) |
| `__PROJECT_PREFIX_UPPER__` | uppercase snake_case of the entry with trailing underscore (`ADD_SUBTRACT_`) |

Files whose path contains `__PROJECT_ENTRY__` (e.g., `src/__PROJECT_ENTRY__.py`, `tests/test___PROJECT_ENTRY__.py`) are renamed to use the snake_case project entry.

## Text vs binary detection

Text files get placeholder substitution; binary files are copied byte-for-byte. Detection uses two heuristics:
- A short suffix denylist (`.png`, `.jpg`, `.jpeg`, `.gif`, `.ico`, `.bin`, `.so`, `.dylib`).
- A 1 KiB sample read; if the sample contains a NUL byte, the file is treated as binary.

## Integration with the classification phase

```python
phase = ClassificationPhase(template_path=Path("/opt/agent-code/templates/python"))
outcome = await phase.run(ctx)
```

When the workspace is detected as EMPTY:

1. The phase reads inputs from the ticket via `extract_inputs_from_ticket`.
2. It materializes the template via `materialize_template`.
3. It re-detects the project type (should now be PYTHON).
4. The classification report records the bootstrap event (`bootstrap.template_version`, `bootstrap.materialized_files`).

When the workspace is EMPTY but `template_path` is None (default), the phase halts with the message:
> Workspace is empty and no template_path was configured for bootstrap.
> Configure `template_path` in config.yaml or pre-populate the workspace.

## What is NOT done by this PR

- **`uv sync`** after materialization: out of scope; needs the tool registry wiring. The materialized project has a `pyproject.toml`; the operator runs `uv sync` themselves for now.
- **Initial git commit**: out of scope; needs the git tools wiring. The materialized files are uncommitted.
- **CLI wiring of `template_path`**: today `agent-code run` does not pass a `template_path` to the orchestrator. Empty workspaces therefore halt with the clear error above. The wiring lands when the CLI consumes `AgentCodeConfig.template_path` (small follow-up).
- **Idempotency on re-run**: if the workspace is already populated after a previous bootstrap, the classification will detect PYTHON and skip the bootstrap path. Re-running on a half-bootstrapped workspace is undefined behavior; cleaning the workspace first is recommended.

## Testing

- `tests/test_bootstrap.py` (16 tests): every aspect of `extract_inputs_from_ticket` (frontmatter parsing, fallbacks, error cases), every aspect of `materialize_template` (substitution, renaming, binary preservation, sorted output, missing template error, missing template version, real `vars/project-template/` fixture), `BootstrapInputs` immutability.
- `tests/test_phases_classification.py` (3 new tests, 12 total): bootstrap on EMPTY workspace, halt on EMPTY without template, halt on bootstrap failure with error captured in the report.
