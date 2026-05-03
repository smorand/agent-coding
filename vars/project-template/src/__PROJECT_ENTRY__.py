"""CLI entry point for the __PROJECT_NAME__ application."""

import logging
from typing import Annotated

import typer

from config import Settings
from logging_config import setup_logging
from tracing import configure_tracing, trace_span

app = typer.Typer(help="__PROJECT_DESCRIPTION__")
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
    """__PROJECT_DESCRIPTION__"""
    settings = Settings()
    setup_logging(app_name=settings.app_name, verbose=verbose, quiet=quiet)
    configure_tracing(app_name=settings.app_name)


@app.command()
def hello(
    name: Annotated[str, typer.Argument(help="Name to greet")] = "World",
) -> None:
    """Print a greeting. Replace this command with your own once the project starts."""
    with trace_span("cli.hello", attributes={"target.name": name}):
        message = f"Hello, {name}!"
        logger.debug("Greeting %s", name)
        typer.echo(message)


if __name__ == "__main__":
    app()
