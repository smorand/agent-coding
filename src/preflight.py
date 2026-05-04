"""Toolchain pre-flight check (FR-015).

Verifies that every binary the agent needs is present on PATH and that
external prerequisites (Python version, GitHub CLI authentication) are met.
The check produces a structured report; if any required item is missing or
broken, the agent stops with exit code 3 and prints actionable instructions.

This module is intentionally synchronous: it runs once at startup, before
the orchestrator, and does not benefit from asyncio. Callers can wrap with
`asyncio.to_thread` if they need to run it from an async context.
"""

from __future__ import annotations

import shutil
import subprocess  # nosec B404 - controlled invocation of `gh auth status` only
import sys
from dataclasses import dataclass, field
from enum import StrEnum

MIN_PYTHON_VERSION: tuple[int, int] = (3, 13)


class CheckSeverity(StrEnum):
    """How a failed check should be reported."""

    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True)
class BinaryRequirement:
    """A binary the agent expects on PATH."""

    name: str
    install_hint: str
    severity: CheckSeverity = CheckSeverity.BLOCKING


@dataclass(frozen=True)
class CheckResult:
    """Outcome of a single pre-flight check."""

    name: str
    ok: bool
    severity: CheckSeverity
    detail: str
    install_hint: str = ""


@dataclass(frozen=True)
class PreflightReport:
    """Aggregate result of all pre-flight checks."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        """True when no blocking failure was recorded."""
        return not any(not r.ok and r.severity == CheckSeverity.BLOCKING for r in self.results)

    @property
    def blocking_failures(self) -> list[CheckResult]:
        """Results that are failing AND blocking."""
        return [r for r in self.results if not r.ok and r.severity == CheckSeverity.BLOCKING]

    @property
    def warnings(self) -> list[CheckResult]:
        """Results that are failing but only warnings."""
        return [r for r in self.results if not r.ok and r.severity == CheckSeverity.WARNING]


REQUIRED_BINARIES: tuple[BinaryRequirement, ...] = (
    BinaryRequirement(
        name="make",
        install_hint="GNU make is the single dev interface; install via your OS package manager.",
    ),
    BinaryRequirement(
        name="git",
        install_hint="Install git via your OS package manager (https://git-scm.com/downloads).",
    ),
    BinaryRequirement(
        name="gh",
        install_hint="Install the GitHub CLI from https://cli.github.com/ then run `gh auth login`.",
    ),
    BinaryRequirement(
        name="uv",
        install_hint="Install uv from https://docs.astral.sh/uv/ (curl -LsSf https://astral.sh/uv/install.sh | sh).",
    ),
    BinaryRequirement(
        name="rg",
        install_hint="Install ripgrep via your OS package manager (e.g., brew install ripgrep).",
    ),
    BinaryRequirement(
        name="ast-grep",
        install_hint="Install ast-grep via cargo (cargo install ast-grep) or your OS package manager.",
    ),
)


def check_python_version(
    current: tuple[int, int] | None = None,
    minimum: tuple[int, int] = MIN_PYTHON_VERSION,
) -> CheckResult:
    """Verify the running Python is at least `minimum`.

    `current` defaults to `sys.version_info[:2]` when None; explicit value is
    used in tests to simulate older runtimes.
    """
    runtime = current if current is not None else (sys.version_info.major, sys.version_info.minor)
    ok = runtime >= minimum
    detail = f"Python {runtime[0]}.{runtime[1]} found"
    if ok:
        return CheckResult(
            name="python",
            ok=True,
            severity=CheckSeverity.BLOCKING,
            detail=detail,
        )
    return CheckResult(
        name="python",
        ok=False,
        severity=CheckSeverity.BLOCKING,
        detail=f"{detail}, need >= {minimum[0]}.{minimum[1]}",
        install_hint=(f"Install Python {minimum[0]}.{minimum[1]}+ via your OS package manager or pyenv."),
    )


def check_binary(requirement: BinaryRequirement) -> CheckResult:
    """Check whether a binary is on PATH."""
    found = shutil.which(requirement.name)
    if found is not None:
        return CheckResult(
            name=requirement.name,
            ok=True,
            severity=requirement.severity,
            detail=f"found at {found}",
        )
    return CheckResult(
        name=requirement.name,
        ok=False,
        severity=requirement.severity,
        detail="not found on PATH",
        install_hint=requirement.install_hint,
    )


def check_gh_authenticated() -> CheckResult:
    """Verify `gh auth status` succeeds (or report a clear failure)."""
    gh_path = shutil.which("gh")
    if gh_path is None:
        return CheckResult(
            name="gh.auth",
            ok=False,
            severity=CheckSeverity.BLOCKING,
            detail="gh binary not found; auth check skipped",
            install_hint="Install gh first (https://cli.github.com/), then run `gh auth login`.",
        )
    try:
        completed = subprocess.run(  # nosec B603 - args are constants, gh_path resolved by which()
            [gh_path, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(
            name="gh.auth",
            ok=False,
            severity=CheckSeverity.BLOCKING,
            detail=f"`gh auth status` failed to execute: {exc}",
            install_hint="Verify gh installation and run `gh auth login`.",
        )
    if completed.returncode == 0:
        return CheckResult(
            name="gh.auth",
            ok=True,
            severity=CheckSeverity.BLOCKING,
            detail="authenticated",
        )
    return CheckResult(
        name="gh.auth",
        ok=False,
        severity=CheckSeverity.BLOCKING,
        detail=f"`gh auth status` exited {completed.returncode}",
        install_hint="Run `gh auth login` to authenticate this machine.",
    )


def run_preflight(
    binaries: tuple[BinaryRequirement, ...] = REQUIRED_BINARIES,
) -> PreflightReport:
    """Run every pre-flight check and aggregate results."""
    results: list[CheckResult] = [check_python_version()]
    for binary in binaries:
        results.append(check_binary(binary))
    results.append(check_gh_authenticated())
    return PreflightReport(results=results)


def format_report(report: PreflightReport) -> str:
    """Render the report as a human-readable multi-line string."""
    lines: list[str] = []
    for result in report.results:
        marker = "OK" if result.ok else "FAIL"
        line = f"[{marker}] {result.name}: {result.detail}"
        if not result.ok and result.install_hint:
            line += f"\n       Hint: {result.install_hint}"
        lines.append(line)
    if report.blocking_failures:
        lines.append("")
        lines.append(f"Pre-flight failed: {len(report.blocking_failures)} blocking issue(s).")
    elif report.warnings:
        lines.append("")
        lines.append(f"Pre-flight passed with {len(report.warnings)} warning(s).")
    else:
        lines.append("")
        lines.append("Pre-flight passed.")
    return "\n".join(lines)
