# Makefile Reference

> The Makefile is the single dev interface for this project. The agent and
> humans use the same `make` targets. NEVER invoke `uv`, `pytest`, `ruff`,
> `mypy`, or `bandit` directly.

## Auto-detected variables

The Makefile detects context automatically:

| Variable | Source | Default |
|---|---|---|
| `PROJECT_NAME` | `name` field in `pyproject.toml` | (required) |
| `ENTRY_POINT` | First `.py` in `src/` other than `__init__.py` | `app` |
| `SRC_DIR` | `src/` if it exists, else `.` | `src` |
| `MAKE_DOCKER_PREFIX` | Environment override | empty |
| `DOCKER_TAG` | Environment override | `latest` |

## Dependency management

| Target | What it runs | When to use |
|---|---|---|
| `sync` | `uv sync` | First clone, after pulling new deps, after editing `pyproject.toml` |

## Running

| Target | What it runs |
|---|---|
| `run` | `uv run <project>` (production-style invocation through the entry script) |
| `run-dev` | `uv run python src/<entry>.py` (direct execution, faster startup) |

Pass arguments with `ARGS=`:

```bash
make run ARGS='--help'
make run ARGS='greet Alice --verbose'
```

## Testing

| Target | What it runs |
|---|---|
| `test` | `uv run pytest -v` |
| `test-cov` | `uv run pytest -v --cov=src --cov-report=term-missing` |

`test-cov` enforces `fail_under = 80` via `pyproject.toml`.

## Code quality

| Target | What it runs | What it checks |
|---|---|---|
| `lint` | `ruff check .` | Style, imports, common bugs |
| `lint-fix` | `ruff check --fix .` | Same, but auto-fixes safe issues |
| `format` | `ruff format .` | Apply formatting |
| `format-check` | `ruff format --check .` | Verify formatting without modifying |
| `typecheck` | `mypy src/` | Strict type checking |
| `security` | `bandit -r src/` | Common security anti-patterns |
| `check` | All of the above (in order) plus `test-cov` | Full quality gate |

`make check` is the **hard quality gate**. Order matters: lint, format-check, typecheck, security, test-cov. ANY non-zero exit fails the gate.

## Build and install

| Target | What it runs |
|---|---|
| `build` | `uv build` (wheel + sdist into `dist/`) |
| `install` | `uv tool install . --reinstall --force` |
| `uninstall` | `uv tool uninstall <project>` |

## Docker

| Target | What it runs |
|---|---|
| `docker-build` | `docker build -t <prefix><project>:<tag> .` |
| `docker-push` | `docker push <prefix><project>:<tag>` |
| `docker` | `docker-build` then `docker-push` |
| `run-up` | Build image, then `docker compose up -d` |
| `run-down` | `docker compose down` |

Example with a private registry:

```bash
MAKE_DOCKER_PREFIX=registry.internal/myteam/ DOCKER_TAG=v1.0.0 make docker
```

## Cleanup

| Target | What it removes |
|---|---|
| `clean` | Caches: `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `dist/`, `build/`, `.coverage`, `htmlcov/` |
| `clean-all` | Everything `clean` does, plus `.venv/` and `uv.lock` |

## Information

| Target | What it shows |
|---|---|
| `info` | Project name, entry point, source dir, Python version, uv availability |
| `help` | One-line description of every target |

## Reading `make check` failures

| Failure source | What the output looks like | Where to fix |
|---|---|---|
| `lint` (Ruff) | `<file>:<line>:<col>: <RULE-CODE> <message>` | Run `make lint-fix` for auto-fixable; otherwise edit the line |
| `format-check` | `Would reformat: <file>` | Run `make format` to apply |
| `typecheck` (mypy) | `<file>:<line>: error: <message> [error-code]` | Add or fix type annotations |
| `security` (bandit) | `Issue: [B<code>] <message> in <file>:<line>` | Replace the unsafe pattern; if false positive, justify with `# nosec B<code>` and a comment |
| `test-cov` | `FAILED tests/test_X.py::test_Y` plus traceback; or `Total coverage: <N>% < 80%` | Fix the failing test or add tests for uncovered lines |

When `make check` fails on multiple stages, fix them in the same order they ran (lint first, then format, then types, then security, then tests). Earlier fixes often resolve later issues.
