# agent-code

Autonomous coding agent that takes a structured user story as input and produces a Pull Request as output, designed for on-premise mid-class open-weight models.

## Status

**Bootstrap phase.** Project skeleton is in place; orchestrator and phase implementations are next. See `specs/` for the MVP specification, `vars/` for the canonical templates the agent consumes.

## Tech stack

Python 3.13+, Typer (CLI), pydantic-settings (config), OpenTelemetry (tracing), Ruff (lint, format), mypy (typing, strict), pytest (tests), bandit (security), uv (package manager).

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- `make` (single dev interface)
- `git`, `gh` (GitHub CLI, authenticated) for PR operations
- `ripgrep`, `ast-grep` for codebase navigation (used by the agent at runtime, not at build)
- Docker (optional, for `make docker-build` and `make run-up`)

## Install

```bash
uv sync
```

## Usage

```bash
make run ARGS='check-env'                       # FR-015 toolchain pre-flight
make run ARGS='config-show --config cfg.yaml'   # FR-002 config validation
make run ARGS='run path/to/ticket.md'           # main pipeline (skeleton phases)
```

- `check-env` audits the host (Python 3.13+, `make`, `git`, `gh` authenticated, `uv`, `rg`, `ast-grep`). Exits 0 if all good, 3 on any blocking failure with actionable hints. See `.agent_docs/preflight.md`.
- `config-show` loads `config.yaml` (via `--config`, `AGENT_CODE_CONFIG`, or `~/.config/agent-code/config.yaml`), validates strictly, and echoes the parsed config. See `.agent_docs/configuration.md`.
- `run` drives the seven-phase pipeline against a ticket and exits with one of the canonical codes (0: completed, 1: DoR failed, 2: implementation exhausted, 3: system error). At this stage every phase is a logging stub; real phase logic lands incrementally. See `.agent_docs/orchestrator.md`.

## Quality gate

```bash
make check
```

Runs lint, format-check, typecheck, security, and tests with coverage (>= 80% required). Must pass before every commit.

## Tests

```bash
make test
make test-cov
```

## Repository layout

```
agent-coding/                        # The repo (= the agent-code project)
├── README.md                        (this file)
├── CLAUDE.md                        AI agent index
├── Makefile                         Single dev interface
├── pyproject.toml                   uv + ruff + mypy + pytest config
├── LICENSE                          MIT
├── Dockerfile, docker-compose.yml   Container build
├── .agent_docs/                     Detailed docs (loaded on demand)
├── src/                             Source (NOT a Python package)
│   ├── agent_code.py                CLI entry point
│   ├── config.py                    Settings (pydantic-settings)
│   ├── logging_config.py            Logging setup (rich + file)
│   └── tracing.py                   OpenTelemetry tracing
├── tests/                           Pytest suite
├── specs/                           Specification documents
│   └── 2026-05-03_21:03:22-agent-code-mvp.md
└── vars/                            Canonical templates shipped with agent-code
    ├── project-template/            Python project skeleton (consumed by FR-014)
    └── ticket-template/             User story templates (consumed by FR-004)
```

## Design principles

1. **Safety over speed.** The agent is designed to be slow and correct, not fast and approximate. It must work on on-premise mid-class open-weight models (Qwen 3 32B class), with no dependency on large proprietary models.
2. **Multi-model orchestration.** Each phase of the pipeline is served by an appropriately sized model declared in a configuration file.
3. **Test-first, anti-cheat.** End-to-end tests are written in an isolated phase before any implementation. Tests are locked read-only during the implementation loop. The Pull Request gate requires 100% of E2E tests passing, non-negotiable.
4. **Self-contained projects.** All coding standards, project conventions, and toolchain instructions live in the project's own `CLAUDE.md` and `.agent_docs/`, populated from `vars/project-template/` at bootstrap. The agent itself carries no language-specific knowledge.
5. **Auditable.** Every step of every run is persisted to `.agent_work/<ticket-id>/` and committed to the feature branch as a single audit trail artifact.

## Pipeline

```
ticket -> classify -> DoR check -> comprehend -> plan -> write E2E (locked) ->
implement loop (multi-approach) -> review (fresh context) -> open PR
```

See `specs/2026-05-03_21:03:22-agent-code-mvp.md` for the complete specification (17 functional requirements, 28 E2E tests, 6 scenarios).

## Documentation

- `CLAUDE.md`: AI agent index and project conventions.
- `.agent_docs/`: detailed documentation, loaded on demand.
- `specs/`: specification documents under git.
- `vars/`: canonical templates shipped with the agent.

## License

MIT, see `LICENSE`.
