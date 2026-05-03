# Project Structure Rules

> Where things live in this project. The agent uses these rules to decide
> where to place new modules, where to extract shared code, and when to split
> a file or create a subpackage.

## Top-level layout

```
project-name/
├── Makefile                  # MUST be at root
├── CLAUDE.md                 # MUST be at root
├── README.md                 # MUST be at root
├── LICENSE                   # MUST be at root
├── pyproject.toml            # MUST be at root
├── uv.lock                   # MUST be at root, COMMITTED
├── Dockerfile                # At root if the project ships a container
├── docker-compose.yml        # At root if local-services orchestration is used
├── .pre-commit-config.yaml   # At root
├── .gitignore                # At root
├── .agent_docs/              # Detailed agent docs
├── src/                      # Source root
└── tests/                    # Test root
```

Forbidden at top level:

- `lib/` (use `src/utils/` for shared utilities)
- `utils/` at top level (use `src/utils/`)
- `main.py`, `cli.py` (use `src/<project>.py`)
- `__init__.py` directly under `src/`

## src/ layout

Two layouts are supported. Pick one at bootstrap and stick to it for the project's lifetime.

### Layout A: flat

Modules directly under `src/`. Simpler for small to medium projects.

```
src/
├── py.typed
├── <project>.py           # Entry point
├── config.py
├── logging_config.py
├── tracing.py
├── models.py              # Optional: Pydantic models
├── services/              # Optional: business logic services
│   ├── __init__.py
│   └── <service>.py
└── utils/                 # Optional: shared utilities
    ├── __init__.py
    └── <util>.py
```

`pyproject.toml`:
```toml
[tool.hatch.build.targets.wheel]
sources = ["src"]
only-include = ["src"]

[project.scripts]
<project> = "<project>:app"
```

Imports: `from greeter import Greeter`, never `from src.greeter import Greeter`.

### Layout B: package

Project-named package under `src/`. Use when the project may grow large or be importable as a library.

```
src/
└── <project>/
    ├── __init__.py
    ├── py.typed
    ├── <project>.py       # Or __main__.py for the entry
    ├── config.py
    ├── logging_config.py
    └── ...
```

`pyproject.toml`:
```toml
[tool.hatch.build.targets.wheel]
packages = ["src/<project>"]

[project.scripts]
<project> = "<project>.<project>:app"
```

Imports: `from <project>.greeter import Greeter`.

NEVER use `packages = ["src"]`; always reference the named package directory.

## Module placement decision tree

When adding new code, ask in order:

1. **Is it pure data (no behavior)?** → `src/models.py` (Pydantic) or alongside the consumer if it is local.
2. **Is it CLI wiring (Typer command, options, callbacks)?** → `src/<project>.py`.
3. **Is it business logic with clear boundaries?** → `src/services/<service>.py`.
4. **Is it a generic helper used by 2+ modules?** → `src/utils/<helper>.py`.
5. **Is it experimental or one-off?** → keep in the calling module. Promote when used twice.

## When to split a file

Split a file when ANY of:

- The file exceeds ~400 lines of substantive code (excluding docstrings and imports).
- Two distinct responsibilities have emerged (the file's docstring becomes "and"-y).
- A subset of the file is needed by another module that does not need the rest.

## When to create a subpackage

Create `src/<area>/` (with `__init__.py`) when:

- 3+ modules belong to the same area (e.g., `src/services/auth.py`, `src/services/billing.py`, `src/services/notifications.py`).
- The area has clear boundaries and an internal API.

Avoid premature subpackaging. One module is fine until you have at least three siblings.

## Tests mirror sources

Every `src/X.py` should have `tests/test_X.py` (when X has logic worth testing). Subpackages mirror: `src/services/auth.py` → `tests/services/test_auth.py`.

Functional or integration tests that span multiple sources go under `tests/functional/`.

## Naming

- Module files: `snake_case.py`.
- Class names: `PascalCase`.
- Function and variable names: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`, defined at module top or in a `constants.py`.
- Type aliases: `PascalCase` if it stands for a type, `snake_case` if it stands for a typed value.
- Test functions: `test_<scenario>_<expected_outcome>`.
