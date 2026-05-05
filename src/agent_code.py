"""CLI entry point for the agent-code application."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer

from config import Settings
from config_loader import AgentCodeConfig, ConfigError, find_config_path, load_config
from llm.factory import PhaseLlmFactory
from logging_config import setup_logging
from mcp.factory import McpClientFactory
from orchestrator import EXIT_OK, EXIT_SYSTEM_ERROR, Orchestrator
from phases import (
    ClassificationPhase,
    ComprehensionPhase,
    DorPhase,
    E2eWritingPhase,
    ImplementationPhase,
    Phase,
    PlanningPhase,
    ReviewPhase,
)
from preflight import format_report, run_preflight
from tools.registry import ToolRegistry
from tracing import configure_tracing

COMPREHENSION_PHASE_NAME = "comprehension"
PLANNING_PHASE_NAME = "planning"

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
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to config.yaml (default: standard lookup)"),
    ] = None,
) -> None:
    """Run the seven-phase pipeline against a ticket and exit with the result code."""
    if not ticket.exists():
        typer.echo(f"Ticket file not found: {ticket}", err=True)
        raise typer.Exit(code=EXIT_SYSTEM_ERROR)
    workspace_resolved = workspace.resolve()
    ticket_id = _ticket_id_from(ticket)
    template_version = _read_template_version(workspace_resolved)
    components = _build_pipeline_components(config)
    exit_code = asyncio.run(
        _run_pipeline(
            components=components,
            workspace=workspace_resolved,
            template_version=template_version,
            ticket_id=ticket_id,
            ticket_path=str(ticket),
        )
    )
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


@dataclass(frozen=True)
class PipelineComponents:
    """Bundle of objects needed to run the orchestrator with optional cleanup.

    `phases` is always populated. `tools` is a `ToolRegistry` carrying the
    MCP tools when the config is loaded successfully (None otherwise).
    `mcp_factory` and `llm_factory` are the lifecycle owners of the
    underlying HTTP connections; the caller is responsible for closing
    both after the run.
    """

    phases: tuple[Phase, ...]
    tools: ToolRegistry | None
    mcp_factory: McpClientFactory | None
    llm_factory: PhaseLlmFactory | None


def _build_pipeline_components(explicit_config: Path | None) -> PipelineComponents:
    """Construct the pipeline phases, tool registry, and LLM factory from `config.yaml`.

    When config is loaded successfully:
    - `template_path` is passed to `ClassificationPhase` (FR-014 bootstrap).
    - The `mcp` section produces an `McpClientFactory`; its three Tool
      adapters are registered in a `ToolRegistry`.
    - A `PhaseLlmFactory` is built; the comprehension phase receives its
      configured `LlmClient` (FR-005). Other LLM-using phases will be
      wired the same way as they are implemented.

    When config is missing or invalid: every phase is at its default,
    the registry is None, and the comprehension phase falls back to
    its skeleton (log + continue) without making an LLM call.
    """
    loaded = _load_config_safely(explicit_config)
    template_path = loaded.template_path if loaded is not None else None
    mcp_factory: McpClientFactory | None = None
    llm_factory: PhaseLlmFactory | None = None
    registry: ToolRegistry | None = None
    if loaded is not None:
        mcp_factory = McpClientFactory.from_config(loaded.mcp)
        registry = ToolRegistry(mcp_factory.build_tools())
        llm_factory = PhaseLlmFactory(loaded)
    comprehension_llm = llm_factory.for_phase(COMPREHENSION_PHASE_NAME) if llm_factory else None
    planning_llm = llm_factory.for_phase(PLANNING_PHASE_NAME) if llm_factory else None
    phases: tuple[Phase, ...] = (
        ClassificationPhase(template_path=template_path),
        DorPhase(),
        ComprehensionPhase(llm_client=comprehension_llm),
        PlanningPhase(llm_client=planning_llm),
        E2eWritingPhase(),
        ImplementationPhase(),
        ReviewPhase(),
    )
    return PipelineComponents(
        phases=phases,
        tools=registry,
        mcp_factory=mcp_factory,
        llm_factory=llm_factory,
    )


def _load_config_safely(explicit_config: Path | None) -> AgentCodeConfig | None:
    """Resolve and load the config; return None on missing/invalid (with a warning)."""
    resolved = find_config_path(explicit_config)
    if resolved is None:
        return None
    try:
        loaded = load_config(resolved)
    except ConfigError as exc:
        logger.warning(
            "Config at %s could not be loaded (%s); proceeding with defaults.",
            resolved,
            exc,
        )
        return None
    logger.info(
        "Loaded config from %s (template_path=%s, mcp.context7=%s, mcp.duckduckgo=%s)",
        resolved,
        loaded.template_path,
        loaded.mcp.context7.url,
        loaded.mcp.duckduckgo.url,
    )
    return loaded


async def _run_pipeline(
    *,
    components: PipelineComponents,
    workspace: Path,
    template_version: str,
    ticket_id: str,
    ticket_path: str,
) -> int:
    """Run the orchestrator, ensuring MCP and LLM connections are closed afterward."""
    orchestrator = Orchestrator(
        workspace=workspace,
        template_version=template_version,
        phases=components.phases,
        tools=components.tools,
    )
    try:
        return await orchestrator.run(ticket_id=ticket_id, ticket_path=ticket_path)
    finally:
        if components.mcp_factory is not None:
            await components.mcp_factory.aclose()
        if components.llm_factory is not None:
            await components.llm_factory.aclose()


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
