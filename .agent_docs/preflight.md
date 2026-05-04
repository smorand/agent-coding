# Toolchain Pre-flight

> Reference for the toolchain check (FR-015). Run via `agent-code check-env`
> at any time; runs implicitly before each pipeline invocation in a future
> PR. Exits 0 on success, 3 on any blocking failure, with actionable hints.

## What is checked

| Check | Severity | Hint on failure |
|---|---|---|
| Python >= 3.13 | blocking | Install Python 3.13+ via OS package manager or pyenv |
| `make` on PATH | blocking | GNU make from OS package manager |
| `git` on PATH | blocking | https://git-scm.com/downloads |
| `gh` on PATH | blocking | https://cli.github.com/, then `gh auth login` |
| `uv` on PATH | blocking | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `rg` (ripgrep) on PATH | blocking | OS package manager (`brew install ripgrep`, etc.) |
| `ast-grep` on PATH | blocking | `cargo install ast-grep` or OS package manager |
| `gh auth status` succeeds | blocking | `gh auth login` |

The list lives in `src/preflight.py::REQUIRED_BINARIES` (a tuple of `BinaryRequirement`). Adding or removing a binary is a one-line change.

LSP servers and MCP endpoints are not yet checked; they will be added in a later PR alongside the tools that call them.

## Output format

`agent-code check-env` prints one line per check, then a summary:

```
[OK] python: Python 3.13 found
[OK] make: found at /usr/bin/make
[OK] git: found at /usr/bin/git
[OK] gh: found at /opt/homebrew/bin/gh
[OK] uv: found at /Users/me/.local/bin/uv
[OK] rg: found at /opt/homebrew/bin/rg
[FAIL] ast-grep: not found on PATH
       Hint: Install ast-grep via cargo (cargo install ast-grep) or your OS package manager.
[OK] gh.auth: authenticated

Pre-flight failed: 1 blocking issue(s).
```

## Adding a new check

1. Add a `BinaryRequirement(name=..., install_hint=...)` to `REQUIRED_BINARIES` in `src/preflight.py`.
2. If the check needs more than `shutil.which`, write a dedicated `check_<x>()` returning `CheckResult` and add it to `run_preflight`.
3. Update `tests/test_preflight.py` with at least one positive and one negative case.
4. Update this file's table and the example output.

## Severity

Two severities: `BLOCKING` (failure causes exit 3) and `WARNING` (logged but does not block). All current binaries are blocking. The mechanism exists for future additions where a tool is recommended but optional (e.g., a profile-specific LSP).

## Programmatic use

The check can be embedded by other code:

```python
from preflight import run_preflight, format_report

report = run_preflight()
if not report.is_ok:
    print(format_report(report))
    sys.exit(3)
```

A future PR will wire this into `agent-code run` so a misconfigured host fails fast before any phase executes.
