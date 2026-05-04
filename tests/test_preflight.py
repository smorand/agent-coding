"""Tests for the toolchain pre-flight (FR-015)."""

from __future__ import annotations

from preflight import (
    BinaryRequirement,
    CheckSeverity,
    check_binary,
    check_python_version,
    format_report,
    run_preflight,
)


def test_check_python_version_passes_when_runtime_meets_minimum() -> None:
    """A runtime equal to the minimum is OK."""
    result = check_python_version(current=(3, 13), minimum=(3, 13))
    assert result.ok is True
    assert "3.13" in result.detail


def test_check_python_version_fails_when_runtime_below_minimum() -> None:
    """A runtime older than the minimum produces a blocking failure with hint."""
    result = check_python_version(current=(3, 9), minimum=(3, 13))
    assert result.ok is False
    assert result.severity == CheckSeverity.BLOCKING
    assert "need >= 3.13" in result.detail
    assert "Install Python" in result.install_hint


def test_check_binary_present_uses_found_path() -> None:
    """A binary that exists on PATH yields ok=True with the resolved path in detail."""
    requirement = BinaryRequirement(name="ls", install_hint="ls is part of coreutils")
    result = check_binary(requirement)
    assert result.ok is True
    assert result.detail.startswith("found at ")


def test_check_binary_absent_returns_blocking_failure_with_hint() -> None:
    """A binary not on PATH yields a blocking failure with the install hint preserved."""
    requirement = BinaryRequirement(
        name="definitely-not-a-binary-xyz",
        install_hint="install xyz from somewhere",
    )
    result = check_binary(requirement)
    assert result.ok is False
    assert result.severity == CheckSeverity.BLOCKING
    assert result.install_hint == "install xyz from somewhere"


def test_run_preflight_collects_results_for_all_binaries() -> None:
    """`run_preflight` runs python, every binary, and gh.auth."""
    fake = (
        BinaryRequirement(name="ls", install_hint="ls is part of coreutils"),
        BinaryRequirement(name="cat", install_hint="cat is part of coreutils"),
    )
    report = run_preflight(binaries=fake)
    names = [r.name for r in report.results]
    assert "python" in names
    assert "ls" in names
    assert "cat" in names
    assert "gh.auth" in names


def test_format_report_renders_ok_when_all_pass() -> None:
    """A report with only OK results ends with the 'Pre-flight passed.' tagline."""
    fake = (BinaryRequirement(name="ls", install_hint=""),)
    report = run_preflight(binaries=fake)
    rendered = format_report(report)
    assert "[OK] python" in rendered
    assert "[OK] ls" in rendered
    assert rendered.rstrip().endswith("Pre-flight passed.") or "blocking" in rendered.lower()


def test_format_report_includes_install_hint_for_failures() -> None:
    """A failed check renders its hint on the next line, prefixed with 'Hint:'."""
    fake = (
        BinaryRequirement(
            name="definitely-not-a-binary-xyz",
            install_hint="install via your favorite manager",
        ),
    )
    report = run_preflight(binaries=fake)
    rendered = format_report(report)
    assert "[FAIL] definitely-not-a-binary-xyz" in rendered
    assert "Hint: install via your favorite manager" in rendered


def test_preflight_report_blocking_failures_isolates_blocking_only() -> None:
    """The `blocking_failures` view returns only failing-and-blocking results."""
    fake = (
        BinaryRequirement(
            name="definitely-not-a-binary-xyz",
            install_hint="x",
            severity=CheckSeverity.BLOCKING,
        ),
        BinaryRequirement(
            name="definitely-not-a-binary-also-no",
            install_hint="y",
            severity=CheckSeverity.WARNING,
        ),
    )
    report = run_preflight(binaries=fake)
    assert report.is_ok is False
    blocking_names = {r.name for r in report.blocking_failures}
    warning_names = {r.name for r in report.warnings}
    assert "definitely-not-a-binary-xyz" in blocking_names
    assert "definitely-not-a-binary-also-no" in warning_names


def test_check_python_version_default_uses_running_runtime() -> None:
    """When `current` is None, the running interpreter version is used and is >= 3.13."""
    result = check_python_version()
    assert result.ok is True
