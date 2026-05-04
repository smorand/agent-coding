"""Configuration loader for agent-code (FR-002).

Reads a YAML configuration file describing the per-phase model endpoints,
loop limits, MCP endpoints, OTel exporter, and secrets denylist. Validates
strictly via Pydantic; the agent fails fast at startup if any required
section is missing or malformed.

The lookup order for the config file is:

1. The explicit `--config <path>` CLI option (handled by the caller).
2. The `AGENT_CODE_CONFIG` environment variable.
3. `$XDG_CONFIG_HOME/agent-code/config.yaml` (or `~/.config/agent-code/config.yaml`).

If none of these resolve to an existing file, `find_config_path` returns
`None` and the caller can decide whether to error out or use defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

CONFIG_FILENAME = "config.yaml"
ENV_CONFIG_PATH = "AGENT_CODE_CONFIG"
ENV_XDG_CONFIG_HOME = "XDG_CONFIG_HOME"
APP_CONFIG_DIRNAME = "agent-code"

PHASE_NAMES_REQUIRED: tuple[str, ...] = (
    "classification",
    "dor",
    "comprehension",
    "planning",
    "e2e_writing",
    "implementation",
    "review",
    "summarizer",
)

DEFAULT_LOOP_MAX_ITERATIONS = 30
DEFAULT_LOOP_STAGNATION_THRESHOLD = 5
DEFAULT_LOOP_MIN_APPROACHES = 3
DEFAULT_LOOP_WALL_CLOCK_SECONDS = 7200

DEFAULT_SECRETS_DENYLIST: tuple[str, ...] = (
    "*_KEY",
    "*_TOKEN",
    "*_SECRET",
    "*_PASSWORD",
)

DEFAULT_OTEL_EXPORTER = "jsonl"
DEFAULT_OTEL_PATH = ".agent_work/{ticket_id}/traces.jsonl"


class ConfigError(Exception):
    """Raised when the configuration is missing, malformed, or unreachable."""


class PhaseModelConfig(BaseModel):
    """Per-phase model endpoint configuration."""

    url: Annotated[str, Field(min_length=1, description="OpenAI-compatible base URL")]
    model_name: Annotated[str, Field(min_length=1)]
    api_key_env: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    @field_validator("url")
    @classmethod
    def _url_has_scheme(cls, value: str) -> str:
        if "://" not in value:
            msg = f"url must include a scheme (got {value!r})"
            raise ValueError(msg)
        return value


class LoopConfig(BaseModel):
    """Limits for the implementation loop (FR-008, FR-009)."""

    max_iterations_per_approach: Annotated[int, Field(gt=0)] = DEFAULT_LOOP_MAX_ITERATIONS
    stagnation_threshold: Annotated[int, Field(gt=0)] = DEFAULT_LOOP_STAGNATION_THRESHOLD
    min_approaches: Annotated[int, Field(gt=0)] = DEFAULT_LOOP_MIN_APPROACHES
    wall_clock_seconds: Annotated[int, Field(gt=0)] = DEFAULT_LOOP_WALL_CLOCK_SECONDS


class McpEndpointConfig(BaseModel):
    """A single MCP server endpoint."""

    url: Annotated[str, Field(min_length=1)]


class McpConfig(BaseModel):
    """All MCP servers consumed by the agent."""

    context7: McpEndpointConfig
    duckduckgo: McpEndpointConfig


class OtelConfig(BaseModel):
    """OpenTelemetry exporter configuration."""

    exporter: Annotated[str, Field(pattern=r"^(jsonl|otlp)$")] = DEFAULT_OTEL_EXPORTER
    path: str = DEFAULT_OTEL_PATH
    endpoint: str | None = None


class AgentCodeConfig(BaseModel):
    """Top-level agent-code configuration loaded from `config.yaml`."""

    phases: dict[str, PhaseModelConfig]
    template_path: Path
    loop: LoopConfig = Field(default_factory=LoopConfig)
    secrets_denylist: list[str] = Field(default_factory=lambda: list(DEFAULT_SECRETS_DENYLIST))
    mcp: McpConfig
    otel: OtelConfig = Field(default_factory=OtelConfig)

    @field_validator("phases")
    @classmethod
    def _phases_complete(cls, value: dict[str, PhaseModelConfig]) -> dict[str, PhaseModelConfig]:
        missing = [name for name in PHASE_NAMES_REQUIRED if name not in value]
        if missing:
            msg = f"phases is missing required entries: {', '.join(missing)}"
            raise ValueError(msg)
        unexpected = [name for name in value if name not in PHASE_NAMES_REQUIRED]
        if unexpected:
            msg = f"phases has unexpected entries: {', '.join(unexpected)}"
            raise ValueError(msg)
        return value


def find_config_path(explicit: Path | None = None) -> Path | None:
    """Resolve the configuration file path, returning None if nothing exists.

    Order: explicit argument, `AGENT_CODE_CONFIG` env var, then the XDG path.
    """
    if explicit is not None:
        return explicit if explicit.exists() else None
    env_value = os.environ.get(ENV_CONFIG_PATH)
    if env_value:
        candidate = Path(env_value)
        if candidate.exists():
            return candidate
    xdg_home = os.environ.get(ENV_XDG_CONFIG_HOME)
    base = Path(xdg_home) if xdg_home else Path.home() / ".config"
    candidate = base / APP_CONFIG_DIRNAME / CONFIG_FILENAME
    return candidate if candidate.exists() else None


def load_config(path: Path | None = None) -> AgentCodeConfig:
    """Load and validate the configuration from `path`.

    Raises `ConfigError` with a precise, actionable message on any failure
    (file not found, unreadable, invalid YAML, schema mismatch).
    """
    resolved = find_config_path(path)
    if resolved is None:
        msg = (
            "No agent-code config found. Pass --config <path>, set "
            f"{ENV_CONFIG_PATH}, or place a file at "
            f"~/.config/{APP_CONFIG_DIRNAME}/{CONFIG_FILENAME}."
        )
        raise ConfigError(msg)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Cannot read config file {resolved}: {exc}"
        raise ConfigError(msg) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        msg = f"Config file {resolved} is not valid YAML: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(data, dict):
        msg = f"Config file {resolved} must be a YAML mapping at the top level"
        raise ConfigError(msg)
    try:
        return AgentCodeConfig.model_validate(data)
    except ValidationError as exc:
        msg = f"Config file {resolved} failed schema validation:\n{exc}"
        raise ConfigError(msg) from exc
