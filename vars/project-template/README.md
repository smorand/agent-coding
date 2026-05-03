# __PROJECT_NAME__

__PROJECT_DESCRIPTION__

## Tech stack

Python 3.13+, Typer (CLI), pydantic-settings (config), OpenTelemetry (tracing), Ruff (lint, format), mypy (typing), pytest (tests), bandit (security).

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- `make` (build interface)

## Install

```bash
uv sync
```

## Usage

```bash
make run                 # Run the CLI
make run ARGS='--help'   # Show CLI help
```

## Quality gate

```bash
make check               # Lint, format, typecheck, security, tests with coverage
```

`make check` must pass before every commit. See `.agent_docs/makefile.md` for all available targets.

## Tests

```bash
make test                # Run tests
make test-cov            # Run with coverage report (>= 80% required)
```

## Documentation

- `CLAUDE.md`: AI agent index and conventions for this project.
- `.agent_docs/`: detailed documentation, loaded by the agent on demand.
- `LICENSE`: MIT.

## License

MIT, see `LICENSE`.
