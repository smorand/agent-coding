# agent-code

Autonomous coding agent that takes a structured user story as input and produces a Pull Request as output, designed for on-premise mid-class open-weight models.

## Status

**MVP feature-complete.** All eight pipeline phases (classification, DoR, comprehension, planning, E2E writing, implementation loop, review, PR creation) implement the spec's `Must-have` requirements: multi-approach exploration (FR-009), rolling-summary context compression (FR-010), wall-clock budget, regression detection, re-run on `REQUEST_CHANGES` (FR-011), audit-trail commits (FR-017), and SHA tampering detection (E2E-026). Tooling includes `apply_patch` (multi-location diffs) and pyright-backed LSP wrappers (`lsp_definition`, `lsp_references`, `lsp_hover`). See `specs/` for the MVP specification, `vars/` for the canonical templates the agent consumes.

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

## Example configs

Two ready-to-use `config.yaml` files live in `examples/` :

- `examples/config.local.yaml` — single vLLM / TGI / llama.cpp endpoint serving a Qwen 3.6 27B (or comparable) model.
- `examples/config.openrouter.yaml` — hosted via OpenRouter with a demo tiering: `qwen/qwen3.5-9b` for deterministic phases, `qwen/qwen3.6-flash` for comprehension and summarizer, `qwen/qwen3.6-27b` for planning / E2E writing / implementation / review.

See `examples/README.md` for the rationale and how to swap models.

## Authoring a ticket

A ticket is a single Markdown file. It MUST pass the Definition-of-Ready phase before any other phase runs; otherwise the agent appends a structured comment to the file and exits with code 1.

### Minimal example that passes the DoR

```markdown
---
id: add-subtract
title: Add a subtract function to calc
author: smorand
---

## Description

The calc module currently exposes only `add`. Add a symmetric `subtract`
function callable from the same module so internal arithmetic helpers
have parity. Validate inputs and raise `TypeError` on non-integers.

## Acceptance Criteria

- AC-1: `calc.subtract(5, 3)` returns the integer `2`.
- AC-2: `calc.subtract(0, 0)` returns the integer `0`.
- AC-3: `calc.subtract(1.5, 0)` raises `TypeError`.
```

Save as `tickets/add-subtract.md`, then `make run ARGS='run tickets/add-subtract.md --config examples/config.openrouter.yaml'`.

### Required structure

| Field | Rule |
|---|---|
| Filename | extension `.md` |
| Frontmatter `id` | matches `^[a-z0-9][a-z0-9-]{2,63}$` (used as the `<ticket-id>` for `.agent_work/<id>/`, branch name, commit messages, PR title) |
| Frontmatter `title` | 5 to 80 characters |
| `## Description` | at least 50 non-whitespace characters of prose explaining the change and its motivation |
| `## Acceptance Criteria` | at least one bullet `- AC-N: <text>`, each criterion 10+ chars and testable (an E2E test must be able to assert it) |
| `## Infrastructure` (optional) | if a line starts with `- requires:` it must have a non-empty value (e.g. `- requires: Postgres 15`) |

Optional sections (`## Out of Scope`, `## Examples`, `## Notes`, `## Dependencies`) are accepted and surfaced to the comprehension and planning phases.

### Where to look for more

- `vars/ticket-template/ticket-example-ready.md` — fully fleshed-out example with frontmatter labels, examples, notes; used as a fixture in the test suite.
- `vars/ticket-template/ticket-example-not-ready.md` — counter-example that intentionally fails the DoR; useful to see what NOT to do.
- `vars/ticket-template/ticket-blank.md` — empty skeleton you can copy.
- `.agent_docs/ticket-template.md` — canonical specification, every section, every rule.
- `.agent_docs/dor.md` — full DoR rules table and the comment format the agent appends on failure.

### Tips

- Make each AC observable. `AC-1: subtract handles edge cases` is too vague; the agent will write a test it cannot reliably pass. Prefer `AC-1: subtract(0, 0) returns the integer 0`.
- List concrete out-of-scope items. The reviewer phase otherwise tends to flag missing functionality.
- Declare every infra dependency. The planning phase will halt with exit 1 if `infra_needs.md` lists e.g. Postgres but `docker-compose.yml` does not declare it.
- Keep the ticket under ~5K tokens. The comprehension phase caps source files at 16 KiB and total context at 64 KiB; longer tickets get truncated.

## Usage

```bash
make run ARGS='version'                                   # print agent-code version
make run ARGS='check-env'                                 # FR-015 toolchain pre-flight
make run ARGS='config-show --config cfg.yaml'             # FR-002 config validation
make run ARGS='run path/to/ticket.md --config cfg.yaml'   # main pipeline
```

- `check-env` audits the host (Python 3.13+, `make`, `git`, `gh` authenticated, `uv`, `rg`, `ast-grep`). Exits 0 if all good, 3 on any blocking failure with actionable hints. See `.agent_docs/preflight.md`.
- `config-show` loads `config.yaml` (via `--config`, `AGENT_CODE_CONFIG`, or `~/.config/agent-code/config.yaml`), validates strictly, and echoes the parsed config. See `.agent_docs/configuration.md`.
- `run` drives the eight-phase pipeline against a ticket. The `--config` flag (or the standard config lookup order) supplies `template_path` so the classification phase can bootstrap an empty workspace from `vars/project-template/` (FR-014). Exit codes: 0 (completed), 1 (DoR failed or planning infra unsatisfiable), 2 (implementation exhausted, E2E tampering), 3 (system error). When run without a config, every LLM-using phase falls back to a no-op skeleton so smoke tests can exercise the pipeline shape. See `.agent_docs/orchestrator.md`, `.agent_docs/dor.md`, `.agent_docs/classification.md`, `.agent_docs/bootstrap.md`, `.agent_docs/comprehension.md`, `.agent_docs/planning.md`, `.agent_docs/e2e-writing.md`, `.agent_docs/implementation.md`, `.agent_docs/review.md`, `.agent_docs/pr-creation.md`.

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
│   ├── tracing.py                   OpenTelemetry tracing
│   ├── llm/                         LLM client (OpenAI-compat, retry, factory)
│   ├── mcp/                         MCP clients (Context7 docs, DuckDuckGo search)
│   ├── phases/                      Seven pipeline phases
│   └── tools/                       Tool registry (file, search, git, make)
├── tests/                           Pytest suite
├── specs/                           Specification documents
│   └── 2026-05-03_21-03-22-agent-code-mvp.md
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

See `specs/2026-05-03_21-03-22-agent-code-mvp.md` for the complete specification (17 functional requirements, 28 E2E tests, 6 scenarios).

## Documentation

- `CLAUDE.md`: AI agent index and project conventions.
- `.agent_docs/`: detailed documentation, loaded on demand.
- `specs/`: specification documents under git.
- `vars/`: canonical templates shipped with the agent.

## License

MIT, see `LICENSE`.
