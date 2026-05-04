# agent-code

> Compact AI agent index. Detailed docs live in `.agent_docs/`. Always read this
> file first; load `.agent_docs/*.md` only when relevant to the current task.
> This project is fully self-documented: no external "skill" or model-side
> instruction file is required. All conventions, standards, and rules the agent
> needs are in this repository.

## What this project does

Autonomous coding agent that takes a structured user story as input and produces a Pull Request as output, designed for on-premise mid-class open-weight models.

## Tech stack

- Python 3.13+
- uv (package manager, lockfile committed for reproducibility)
- Typer (CLI)
- pydantic-settings (configuration)
- OpenTelemetry (tracing, mandatory)
- Ruff (lint and format)
- mypy (typing, strict mode)
- pytest (tests)
- bandit (security scan)

## Key commands

| Command | Purpose |
|---|---|
| `make sync` | Install or refresh dependencies |
| `make run` | Run the CLI entry point |
| `make run-dev` | Run entry point directly (development) |
| `make test` | Run pytest |
| `make test-cov` | Run pytest with coverage (>= 80% required) |
| `make check` | Full quality gate: lint, format-check, typecheck, security, test-cov |
| `make build` | Build wheel and sdist |
| `make docker-build` | Build the Docker image |
| `make help` | List all available targets |

**Always use `make`. NEVER invoke `uv`, `pytest`, `ruff`, `mypy`, or `bandit` directly.** This standardization is what allows the agent and humans to work interchangeably.

## Essential conventions

- `src/` is a source directory, NOT a Python package. **No `src/__init__.py`.**
- Imports use top-level module names. **Never the `src.` prefix.**
- Entry point: `src/<project>.py`. **Never `main.py`, never `cli.py`.**
- Tests parallel the source layout under `tests/`. Test files: `tests/test_*.py`.
- Every new public function or class MUST have type annotations and a one-line docstring.
- Every commit MUST leave the branch with `make check` green.
- The agent NEVER modifies files matching `tests/test_*.py` once they are committed in the E2E phase. Fixtures under `tests/testdata/` and shared `tests/conftest.py` are exceptions (writable).

## Quality gate (non-negotiable)

`make check` runs in this order, and ANY failure blocks a commit or PR:

1. `lint` (Ruff)
2. `format-check` (Ruff)
3. `typecheck` (mypy strict)
4. `security` (bandit)
5. `test-cov` (pytest with coverage >= 80%)

In addition to `make check`, the end-to-end test suite under `tests/` MUST pass at 100%. No partial pass, no skipped E2E.

## Documentation index

Read these on demand. Each file is focused and under ~250 lines.

- `.agent_docs/python.md`: complete Python coding standards, idioms, forbidden practices, library preferences. **Read before writing any non-trivial Python.**
- `.agent_docs/makefile.md`: per-target Makefile reference and how to read failure output.
- `.agent_docs/testing.md`: pytest conventions, fixtures, what counts as E2E in this project, the read-only rule on `tests/test_*.py`.
- `.agent_docs/structure.md`: directory and file placement rules, naming, when to split a module.
- `.agent_docs/tooling.md`: every tool the agent has access to (read, edit, shell, git, MCP) and when to use each.
- `.agent_docs/ticket-template.md`: canonical user story Markdown template (DoR validation reference).
- `.agent_docs/pr-template.md`: canonical Pull Request description template.
- `.agent_docs/orchestrator.md`: state machine, `State` schema, atomic persistence, resume semantics, phase contract.
- `.agent_docs/configuration.md`: `config.yaml` schema, lookup order, validation rules, examples.
- `.agent_docs/preflight.md`: required host binaries, `agent-code check-env` output format, how to add a check.
- `.agent_docs/llm.md`: `LlmClient` interface, OpenAI-compat client, retry policy, per-phase factory, OTel attributes (no prompts/responses).
- `.agent_docs/tools.md`: tool Protocol, ToolRegistry, file/search/git/make wrappers, injectable subprocess runner.

## Where to write what

- New module logic: under `src/`, mirroring an existing similar module.
- New tests: under `tests/`, mirroring the source layout.
- New project conventions discovered or decided during implementation: append to the relevant `.agent_docs/*.md` file. If a new topic is introduced, create `.agent_docs/<topic>.md` and add an index entry above.
- README updates: mandatory when the user-visible behavior changes (new CLI commands, new config, new behavior).

## Repository-specific top-level directories (`specs/` and `vars/`)

This repo is the `agent-code` project AND the home of its specification and reference templates. Two extra top-level directories exist beyond the standard project layout:

- `specs/`: specification documents (Markdown). NEVER modify existing specs; new specs are added with timestamped filenames. The spec is the source of truth for what the agent must do; align changes against it.
- `vars/`: canonical templates that `agent-code` consumes at runtime.
  - `vars/project-template/`: the Python project skeleton used by the bootstrap phase (FR-014).
  - `vars/ticket-template/`: the user story templates used by the DoR phase (FR-004).
  - When changing rules in `vars/project-template/CLAUDE.md` or `vars/project-template/.agent_docs/`, also update the matching files at the repo root if the rules apply to this project too. The two are intentionally similar (this project follows its own template) but distinct files; do not symlink.

## Auto-evaluation checklist

Before considering any task complete:

- [ ] `make check` exits 0
- [ ] 100% of E2E tests pass (no skipped, no xfail without explicit justification)
- [ ] No sync blocking calls inside async code (no `requests`, no `subprocess.run` in hot paths, no plain `open()` in async hot paths)
- [ ] OpenTelemetry tracing on all external calls (HTTP, DB, tools, LLM)
- [ ] No forbidden practices (bare `except`, `print()`, mutable defaults, `.format()`, `assert` in non-test code)
- [ ] Configuration via `Settings` class (pydantic-settings), not direct `os.environ` access
- [ ] Dependencies injected, not created inside classes
- [ ] Test coverage >= 80%
- [ ] `CLAUDE.md` and relevant `.agent_docs/*.md` reflect any new convention
- [ ] `README.md` updated if user-visible behavior changed

## Audit trail (`.agent_work/`)

When this project is worked on by `agent-code`, the agent writes its plan, attempts log, decisions, and intermediate state to `.agent_work/<ticket-id>/`. This directory is tracked in git on the feature branch (intentionally NOT in `.gitignore`) and collapses on squash-merge into main. Do not delete it during a working session.
