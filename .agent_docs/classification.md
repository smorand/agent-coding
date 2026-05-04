# Classification Phase

> Pipeline phase 1 (FR-003). Pure file-system inspection of the workspace
> identifies the project type. Deterministic; the spec reserves a small-model
> disambiguation step for edge cases, but the MVP rules below cover every
> case the spec contemplates. Halts the pipeline with an actionable message
> when the project is not Python.

## Files

- `src/phases/project_detector.py`: pure detection logic. `detect_project_type(workspace, *, ticket_path=None) -> DetectionResult`. No I/O beyond directory listing.
- `src/phases/classification.py`: `ClassificationPhase` (replaces the prior stub). Reads via `asyncio.to_thread`, persists `classification.json` under `.agent_work/<ticket_id>/`, returns CONTINUE on supported types and HALT_ERROR otherwise.

## Detection rules (priority order)

| Marker file | Detected type | Supported in MVP |
|---|---|---|
| `pyproject.toml` | `python` | yes |
| `package.json` | `node` | no |
| `Cargo.toml` | `rust` | no |
| `go.mod` | `go` | no |
| `pom.xml`, `build.gradle`, `build.gradle.kts` | `java` | no |
| (none of the above + workspace is empty) | `empty` | yes (bootstrap target) |
| (no markers + non-empty workspace) | `unknown` | no |

`pyproject.toml` wins over any other marker (a polyglot repo with both Python and Node sources is treated as Python).

## Empty workspace heuristic

Top-level entries that do NOT count toward "non-empty":

- `.git`, `.github`, `.gitignore`, `.agent_work`
- `specs`, `vars`, `tickets`
- `README.md`, `LICENSE`
- The ticket file itself (passed as `ticket_path`)

This lets a developer initialize an empty repo, drop a ticket file in, and run `agent-code` to bootstrap the project from `vars/project-template/` (FR-014, lands in a future PR).

## Output

`PhaseOutcome.kind`:

- `CONTINUE` when `result.is_supported` is True (Python or Empty).
- `HALT_ERROR` otherwise; `outcome.message` carries an actionable text:
  - "Detected project type 'rust' (markers: Cargo.toml). Only ['empty', 'python'] are supported in this MVP."
  - "Could not determine project type (no Python markers like pyproject.toml found, workspace is not empty). Only ['empty', 'python'] are supported in this MVP."

## Persisted report

`.agent_work/<ticket_id>/classification.json` is written on every run:

```json
{
  "project_type": "rust",
  "markers": ["Cargo.toml"],
  "is_supported": false,
  "supported_types": ["empty", "python"]
}
```

## Workspace inference

The phase derives the workspace as `ctx.work_dir.parent.parent`. This matches the `workspace/.agent_work/<ticket_id>/` layout established by the orchestrator. Tests that bypass the orchestrator must construct `work_dir` accordingly.

## Testing

- `tests/test_project_detector.py` (15 tests): every marker case (parametrized for Java), Python priority, empty heuristic, ticket-file exclusion, missing/non-directory paths, ignored top-level entries, immutability of `DetectionResult`, supported set invariant.
- `tests/test_phases_classification.py` (6 tests): Python workspace, empty workspace, Node/unknown rejection, report persistence on success and failure, work_dir auto-creation.

## What is NOT covered yet

- **LLM disambiguation**: the spec mentions using a small model for edge cases. MVP does not invoke any model; the deterministic rules suffice for every test case shipped here.
- **Reading ticket text for hints**: the spec mentions cross-checking the ticket description. Not used today; would be a small addition once a phase tries to disambiguate, e.g., between Python and Empty.
- **Sub-language detection** (Python 2 vs 3, asyncio version, etc.): out of MVP scope. The Python skill rules in `.agent_docs/python.md` already pin Python 3.13+.
- **Multi-package workspaces** (a monorepo with both `python/` and `web/`): treated by top-level markers only.
