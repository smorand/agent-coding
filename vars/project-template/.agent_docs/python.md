# Python Coding Standards

> Complete reference for Python development in this project. The agent reads
> this file before writing any non-trivial code. No external skill is required;
> everything below is canonical for this project.

## Project layout

```
project-name/
├── Makefile                  # Build automation (single dev interface)
├── CLAUDE.md                 # AI agent compact index
├── README.md                 # Human-facing documentation
├── LICENSE                   # MIT
├── .agent_docs/              # Detailed agent docs (this file lives here)
├── .gitignore
├── pyproject.toml            # Single source of project configuration
├── uv.lock                   # COMMITTED for reproducibility
├── Dockerfile                # Multi-stage build
├── docker-compose.yml
├── .pre-commit-config.yaml
├── src/                      # Source directory (NOT a Python package)
│   ├── py.typed              # PEP 561 marker
│   ├── <project>.py          # Entry point
│   ├── config.py             # Settings (pydantic-settings)
│   ├── logging_config.py     # Logging setup (rich + file)
│   ├── tracing.py            # OpenTelemetry tracing
│   └── <modules>.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── testdata/             # Golden files
    └── test_*.py
```

### `src/` rules

- `src/` is a source directory. **`src/__init__.py` MUST NOT exist.**
- Imports use top-level module names. **Imports MUST NOT use the `src.` prefix.**
- Entry point file MUST be named after the project (e.g., `src/agent_code.py` for project `agent-code`). **Never `main.py`. Never `cli.py`.**
- Two layouts are allowed; pick one and stick to it:
  - **Layout A (flat):** modules directly in `src/`. `pyproject.toml` uses `sources = ["src"]` and `only-include = ["src"]`.
  - **Layout B (package):** project-named package under `src/<project>/`. `pyproject.toml` uses `packages = ["src/<project>"]`.
- Tests parallel the source layout under `tests/`.

### Forbidden directories

- No `/lib` or `/utils` at top level. If you need utilities, use `src/utils/`.

## Mandatory frameworks

| Purpose | MUST use | NEVER use |
|---|---|---|
| CLI | Typer | argparse, click |
| Web API | FastAPI (OOP, class-based, with `Depends`) | Flask, Django |
| Configuration | pydantic-settings | direct `os.environ`, plain dotenv |
| Linting and formatting | Ruff | Black, isort, Flake8 |
| Type checking | mypy (strict) | pyright (in this project) |
| Testing | pytest | unittest |
| Security scan | bandit | none |

## Async first

**Always use asyncio. NEVER mix sync blocking calls in async code.**

| Category | MUST use | NEVER use |
|---|---|---|
| HTTP client | httpx (async), aiohttp | requests |
| Database (Postgres) | asyncpg | psycopg2 |
| Database (MySQL) | aiomysql | mysql-connector |
| Database (SQLite) | aiosqlite | sqlite3 (sync, in async contexts) |
| Redis | redis.asyncio | redis (sync) |
| File I/O (hot paths) | aiofiles, `asyncio.to_thread()` | `open()` |
| Task queue | arq, aio-pika | celery |
| Subprocess | `asyncio.create_subprocess_exec` | `subprocess.run` (unwrapped) |
| Socket | `asyncio.open_connection` | `socket.connect/send/recv` |

HTTP clients MUST use OpenTelemetry-instrumented variants. If no async library exists for a given dependency, wrap it with `asyncio.to_thread()`.

## Concurrency patterns

- MUST use `asyncio.TaskGroup` for parallel tasks. NEVER use `asyncio.gather`.
- MUST use `asyncio.Semaphore` for concurrency limiting.
- MUST handle signals for graceful shutdown of the event loop.
- MUST handle `asyncio.CancelledError` for task cancellation.

## Configuration management

MUST use `pydantic-settings`. Every project needs `src/config.py` exposing a `Settings` class:

- Inherits from `BaseSettings`.
- Uses `SettingsConfigDict` with `env_prefix` matching the app name (uppercase, trailing underscore: e.g., `AGENT_CODE_`).
- Loads `.env` files automatically.
- NEVER access `os.environ` directly anywhere in the code.

Settings are instantiated once in the entry point and injected everywhere.

## Logging

- MUST use `rich` for colored console output.
- MUST write logs to `<app_name>.log` via a file handler.
- MUST have `src/logging_config.py` with `setup_logging()`. The template provides a working version.
- MUST expose `-v` (verbose) and `-q` (quiet) options in the CLI.
- Use `logger = logging.getLogger(__name__)` at module top.
- Use `%` formatting for log messages (lazy evaluation): `logger.info("Loaded %s items", count)`. NEVER f-strings inside log calls.
- Use `typer.echo()` for CLI user-facing output. NEVER `print()`.

## OpenTelemetry tracing (mandatory)

- MUST have `src/tracing.py` with `configure_tracing()` and `trace_span()`. Template provides a working version.
- Default exporter writes JSONL to `<app_name>-otel.log`.
- Span naming convention: `category.operation` (e.g., `api.search`, `db.query`, `llm.call`).
- API calls MUST transmit the `traceparent` header.
- FastAPI: use `opentelemetry-instrumentation-fastapi`.
- HTTP clients: use `opentelemetry-instrumentation-httpx` or `opentelemetry-instrumentation-aiohttp-client`.

### What MUST be traced

| Category | Level | Span attributes |
|---|---|---|
| API calls (HTTP, gRPC) | INFO | endpoint, method, status_code, duration |
| External tool calls | INFO | tool name, arguments summary, result summary |
| Database queries | DEBUG | query preview (200 chars), row_count, duration |
| File mutations | DEBUG | path, size or count |
| Auth operations | INFO | operation type, success or failure |
| Errors | ERROR | message, plus `span.record_exception(e)` |
| LLM calls | INFO | model, input_tokens, output_tokens, duration, cost |

### What MUST NEVER be traced

- Credentials, API keys, tokens, passwords.
- PII (names, emails, addresses, phone numbers).
- LLM prompts and responses (model name and token counts only).
- Raw request bodies that may contain any of the above.

## File structure order

Every `.py` file MUST follow this order:

1. Module docstring.
2. `from __future__ import annotations` (when needed for forward refs).
3. Standard library imports.
4. Third-party imports.
5. Local imports.
6. Module-level constants.
7. Type aliases.
8. Exception classes.
9. Dataclasses or Pydantic models.
10. Protocols or ABCs.
11. Implementation classes (constructor first, methods alphabetically).
12. Module-level functions (ordered by call order, top to bottom).
13. `if __name__ == "__main__":` block.

## Naming conventions

- Boolean: `is_`, `has_`, `should_` prefixes.
- Functions: verb or verb plus noun.
- No abbreviations (allowed: `id`, `api`, `db`).
- No context repetition from the parent scope (e.g., `User.user_name` is wrong; `User.name`).
- Plurals: `users` (list of users), `user_list` (specifically a list wrapper), `user_set` or `user_map` (specific containers).
- Module names: snake_case.
- Class names: PascalCase.
- Constants: UPPER_SNAKE_CASE.

## Design principles

- One function, one responsibility. If the name needs "and" or "or", split the function.
- Class-based design with Single Responsibility Principle.
- Immutable value objects: MUST use `@dataclass(frozen=True)`.
- Maximum 2 levels of conditional or loop nesting. Use early return to reduce depth.
- Side effects MUST be explicit in function names (`save_user`, not `process`).
- Constants over magic values (top of file or in a `constants` module).
- Functions ordered by call order (top to bottom in the file).

## String formatting

- f-strings for value interpolation: `f"Hello {name}"`.
- `%` style for logging (lazy): `logger.info("Hello %s", name)`.
- NEVER use `.format()`. Use f-strings instead.

## Error handling

- Handle errors where a meaningful response is possible. Otherwise let them propagate.
- Technical details go to logs; actionable guidance goes to user-facing messages.
- Distinguish expected errors (recoverable, possibly retried) from unexpected (logged, surfaced).
- Add context when propagating: `raise FooError("loading user X") from exc`.
- Recover from expected errors with a fallback when the recovery is meaningful.

## Dependency injection

- CLI: centralize all dependency creation in `main()` or the Typer callback. Pass dependencies explicitly to commands.
- FastAPI: MUST use the `Depends` pattern with class-based routes (not bare functions).
- NEVER create instances inside classes; always inject them via the constructor.
- Use a Factory pattern when dynamic creation is required.

## Forbidden practices

| Practice | Rule |
|---|---|
| Bare `except:` | Always specify the exception type and log it |
| `# type: ignore` without comment | MUST add a justification: `# type: ignore[arg-type]  # reason` |
| Mutable default arguments | Use `None` and assign in body, or `field(default_factory=list)` |
| Wildcard imports | NEVER `from x import *` |
| `assert` in production code | Use `raise ValueError(...)`. `assert` is only for tests. |
| `print()` | Use `logger.debug()` or `typer.echo()` |
| Mutable global variables | Use dependency injection |
| `.format()` | Use f-strings |
| String concatenation in loops | Use `"".join(parts)` or `io.StringIO` |
| Catching `Exception` to ignore | Always log and decide whether to re-raise |

## Performance

| Anti-pattern | Fix |
|---|---|
| Lists for large streaming data | Use generators or `Iterator[T]` |
| Frequently instantiated classes without `__slots__` | Add `__slots__ = ("_a", "_b", ...)` |
| `list.insert(0, x)` in loops | Use `collections.deque` |
| Repeated `in` lookups on a list | Convert to `set` once |
| `time.sleep()` in async | `await asyncio.sleep()` |
| `open()` in async hot paths | `aiofiles` or `asyncio.to_thread()` |

## Security

- No credentials in code. Always via environment variables loaded by `Settings`.
- Validate all external inputs with Pydantic models.
- `.env` files in `.gitignore` (NEVER committed).
- `bandit` runs in the quality gate (`make security`); it MUST pass.

## Testing

- Focus on functional and end-to-end tests. Unit tests are useful when isolating an algorithm.
- Generate test data via fixtures or factories. Avoid hardcoded inline values that drift.
- Shared fixtures live in `tests/conftest.py`.
- Golden files live under `tests/testdata/`.
- `make test-cov` enforces >= 80% coverage.
- See `.agent_docs/testing.md` for the full testing reference, including the agent's read-only rule on `tests/test_*.py`.

## Modularization

- ZERO TOLERANCE for code duplication.
- Same file: extract a function. Multiple files in the same project: extract a module. Multiple projects: extract a package.
- Module organization suggestion: `<project>.py` (entry), `config.py`, `models.py`, `services/`, `utils/`.

## Build version injection

Every Python application MUST expose its version at runtime.

```python
# src/<project>/version.py
__version__: str = "dev"  # overridden at build time
```

The Makefile derives the version from git tags:

```makefile
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
```

The `build` target writes the version before packaging:

```makefile
build:
	@echo '__version__ = "$(VERSION)"' > src/<project>/version.py
	uv build
```

The Dockerfile accepts `APP_VERSION` as a build argument:

```dockerfile
ARG APP_VERSION=dev
RUN echo "__version__ = \"${APP_VERSION}\"" > src/<project>/version.py
```

If the project has an HTTP layer, expose `GET /health` with the version:

```python
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
```

## Recommended libraries

| Purpose | Library |
|---|---|
| CLI | typer |
| API | fastapi, uvicorn |
| HTTP client | httpx (async), aiohttp |
| Validation | pydantic |
| Configuration | pydantic-settings |
| Database (async) | asyncpg, aiosqlite, aiomysql |
| Testing | pytest, pytest-asyncio, pytest-cov, respx |
| Logging | rich |
| Tracing | opentelemetry-api, opentelemetry-sdk, opentelemetry-instrumentation-* |
| Security scan | bandit |
| Lint and format | ruff |
| Type check | mypy |

## HTTP route structure (when applicable)

When the project serves any combination of MCP, REST API, or Web interfaces:

| Prefix | Role | Auth |
|---|---|---|
| `/mcp` | MCP server (Streamable HTTP) | OAuth2 Bearer |
| `/api/v1/` | REST API (versioned) | Bearer token, session cookie, or OAuth2 |
| `/api/v1/docs` | Swagger UI (auto with FastAPI) | None |
| `/app/` | Web UI (Jinja2 or SPA) | Session cookie |
| `/auth/` | Session login, callback, logout | None |
| `/oauth/` | OAuth2 endpoints | None |
| `/.well-known/` | OAuth2 discovery (RFC 9728, 8414) | None |
| `/health` | Health check + version | None |
| `/metrics` | Prometheus metrics | None |

API versioning is mandatory: always `/api/v{N}/`, never `/v1/` at root. Use `APIRouter(prefix="/api/v1")`.

## Context7 workflow

When adding or updating dependencies, the agent uses the Context7 MCP:

1. `resolve-library-id` to find the canonical library identifier.
2. `query-docs` to get the latest version and current usage patterns.
3. Add to `pyproject.toml` with the verified version.
4. `uv sync` then `make check`.

## Post-implementation checklist

After implementing any feature, ALL of the following MUST be true. The agent's reviewer phase verifies them. The hard quality gate (`make check` plus 100% E2E pass) is non-negotiable.

- [ ] `make check` passes (lint, format-check, typecheck, security, test-cov)
- [ ] 100% of E2E tests pass
- [ ] No sync I/O in async code
- [ ] OpenTelemetry tracing on all external calls
- [ ] No forbidden practices (bare except, print, mutable defaults, .format(), assert)
- [ ] Configuration via the `Settings` class, not `os.environ`
- [ ] Dependency injection used (no instance creation inside classes)
- [ ] Logging uses `%` formatting in log calls (no f-strings inside `logger.x(...)`)
- [ ] `__slots__` on data-heavy or frequently instantiated classes
- [ ] All new code has tests (coverage >= 80%)
- [ ] `CLAUDE.md` and `.agent_docs/*.md` updated if structure or conventions changed
- [ ] `README.md` updated if user-visible behavior changed
