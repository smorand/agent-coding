"""CLI entry point for the agent-code application."""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Annotated

import typer

from config import Settings
from config_loader import ConfigError, load_config
from logging_config import setup_logging
from orchestrator import EXIT_OK, EXIT_SYSTEM_ERROR, Orchestrator
from preflight import format_report, run_preflight
from tracing import configure_tracing

TEMPLATE_VERSION_FILENAME = ".template_version"
DEFAULT_TEMPLATE_VERSION = "unknown"

app = typer.Typer(
    help=(
        "Autonomous coding agent that takes a structured user story as input "
        "and produces a Pull Request as output, designed for on-premise "
        "mid-class open-weight models."
    )
)
logger = logging.getLogger(__name__)


@app.callback()
def main(
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable debug logging"),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Only show warnings and errors"),
    ] = False,
) -> None:
    """Initialize logging and tracing for the CLI invocation."""
    settings = Settings()
    setup_logging(app_name=settings.app_name, verbose=verbose, quiet=quiet)
    configure_tracing(app_name=settings.app_name)


@app.command()
def run(
    ticket: Annotated[
        Path,
        typer.Argument(help="Path to the user story Markdown file"),
    ],
    workspace: Annotated[
        Path,
        typer.Option("--workspace", "-w", help="Project root (default: current directory)"),
    ] = Path(),
) -> None:
    """Run the seven-phase pipeline against a ticket and exit with the result code."""
    if not ticket.exists():
        typer.echo(f"Ticket file not found: {ticket}", err=True)
        raise typer.Exit(code=EXIT_SYSTEM_ERROR)
    workspace_resolved = workspace.resolve()
    ticket_id = _ticket_id_from(ticket)
    template_version = _read_template_version(workspace_resolved)
    orchestrator = Orchestrator(workspace=workspace_resolved, template_version=template_version)
    exit_code = asyncio.run(orchestrator.run(ticket_id=ticket_id, ticket_path=str(ticket)))
    raise typer.Exit(code=exit_code)


@app.command(name="check-env")
def check_env() -> None:
    """Run the toolchain pre-flight (FR-015) and exit 0 on success, 3 on blocking failure."""
    report = run_preflight()
    typer.echo(format_report(report))
    raise typer.Exit(code=EXIT_OK if report.is_ok else EXIT_SYSTEM_ERROR)


@app.command(name="config-show")
def config_show(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config.yaml (default: standard lookup)"),
    ] = None,
) -> None:
    """Load and validate the configuration, then echo the parsed values."""
    try:
        loaded = load_config(config)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=EXIT_SYSTEM_ERROR) from exc
    typer.echo(loaded.model_dump_json(indent=2))


def _read_template_version(workspace: Path) -> str:
    candidate = workspace / TEMPLATE_VERSION_FILENAME
    if not candidate.exists():
        return DEFAULT_TEMPLATE_VERSION
    return candidate.read_text(encoding="utf-8").strip() or DEFAULT_TEMPLATE_VERSION


def _ticket_id_from(ticket: Path) -> str:
    """Derive a slug ticket id from the file stem.

    Falls back to "ticket" if the stem is empty after sanitization.
    """
    stem = ticket.stem
    slug = re.sub(r"[^a-z0-9-]+", "-", stem.lower()).strip("-")
    return slug or "ticket"


if __name__ == "__main__":
    app()
