# Testing Conventions

> How tests are organized, written, and run in this project. Includes the
> non-negotiable read-only rule on `tests/test_*.py` during the agent's
> implementation phase.

## Test taxonomy

This project distinguishes:

- **End-to-end tests (E2E)**: validate the user-visible behavior described by an acceptance criterion. They exercise the system as close to real conditions as possible. They are the primary contract.
- **Functional / integration tests**: validate a subsystem (a module, a service) including its real dependencies (real DB, real HTTP, real subprocesses) when feasible. Acceptable when E2E would be too coarse.
- **Unit tests**: validate a single function or class in isolation. Use only when the unit isolates a non-trivial algorithm where granular coverage adds value. The agent decides when this is justified.

The hard contract is: **100% of E2E tests must pass before a Pull Request can be opened.** Coverage >= 80% is enforced by `make test-cov` (any test category counts).

## File organization

```
tests/
├── __init__.py             # Empty, marks tests as a package
├── conftest.py             # Shared fixtures, accessible to all test files
├── testdata/               # Golden files: JSON, YAML, text fixtures
│   └── ...
├── test_<module>.py        # Tests for src/<module>.py
└── functional/             # Optional: integration tests that need real services
    ├── __init__.py
    └── test_<feature>.py
```

Test file naming: `test_<module>.py`. Test function naming: `test_<scenario>_<expected>` (e.g., `test_subtract_returns_difference`, `test_subtract_rejects_non_integer`).

## Fixtures

- Shared fixtures go in `tests/conftest.py`.
- Local fixtures (used in one file) can stay in that file.
- Async fixtures: use `@pytest_asyncio.fixture` (configured via `asyncio_mode = "auto"` in `pyproject.toml`).

Example shared fixture:

```python
# tests/conftest.py
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(base_url="http://localhost:8000") as client:
        yield client
```

## Parametrize for table-driven tests

```python
import pytest

@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (5, 3, 2),
        (0, 0, 0),
        (-1, -1, 0),
    ],
)
def test_subtract(a: int, b: int, expected: int) -> None:
    assert subtract(a, b) == expected
```

## Mocking policy

- Allowed: mocking external boundaries (filesystem in unit-style tests, HTTP via `respx`, subprocesses via `unittest.mock`, time via `freezegun`).
- Forbidden: mocking the unit under test. If you find yourself mocking what you are testing, you are testing the mock.
- For async code: use `unittest.mock.AsyncMock`.

## Acceptance criteria mapping

E2E tests written from a ticket's acceptance criteria MUST reference them in a comment:

```python
def test_subtract_basic_case() -> None:
    # AC-1: calc.subtract(5, 3) returns 2
    assert subtract(5, 3) == 2
```

This trace allows the reviewer agent to verify that every acceptance criterion has at least one test.

## The read-only rule on `tests/test_*.py`

When this project is operated by `agent-code`, the following rule is enforced at the tool wrapper level:

> Once the E2E test files have been committed in the dedicated test-writing
> phase, the agent CANNOT modify any file matching `tests/test_*.py` until the
> end of the implementation phase. Attempts are rejected with a clear error
> and recorded in `.agent_work/<ticket-id>/attempts.log` as `blocked_tool_call`
> events. Repeated attempts trigger an approach reset.

Exceptions (still writable during implementation):

- `tests/conftest.py`: shared fixtures, including new fixtures needed by the implementation.
- `tests/testdata/**`: golden files, JSON fixtures, sample inputs.
- `tests/functional/**` and `tests/__init__.py`: not matched by `tests/test_*.py`, but the same intent applies; do not weaken assertions.

This rule exists to prevent the most common form of LLM "cheating": modifying tests until they pass instead of fixing the implementation. The agent's success criterion is "the tests as written pass", not "any tests pass".

The rule is enforced regardless of how the agent attempts to bypass it (`edit_file`, `write_file`, `apply_patch`, `run_shell` with `sed`, etc.).

## Coverage

- `make test-cov` runs pytest with `--cov=src --cov-report=term-missing`.
- Branch coverage is enabled.
- Minimum required: 80% (configured in `pyproject.toml` `[tool.coverage.report] fail_under = 80`).
- `__init__.py` files and `tests/*` are excluded from the coverage source.

If coverage is below 80%, add tests covering the missing branches (visible in the `term-missing` output). Do not lower the threshold.

## Performance, security, and contract tests

- Performance baseline tests (e.g., "endpoint returns in under 200ms") belong under `tests/` with the `@pytest.mark.slow` marker if they are slow. Slow tests should be filterable with `pytest -m "not slow"` for local fast feedback.
- Security tests (e.g., "endpoint rejects invalid token") are regular tests; mark them with `@pytest.mark.security` if you want a dedicated bucket.
- Contract tests (e.g., HTTP API schema) belong with the API tests, using `respx` or actual HTTPX calls against a TestClient.

## Common pytest invocations

```bash
make test                          # All tests
make test ARGS='-k test_subtract'  # Filter by name
make test ARGS='-x'                # Stop at first failure
make test ARGS='-vv'               # Extra verbosity
make test-cov                      # With coverage report
```

NEVER invoke `uv run pytest` directly. Always go through `make`.
