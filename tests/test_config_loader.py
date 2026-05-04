"""Tests for the configuration loader (FR-002)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config_loader import (
    APP_CONFIG_DIRNAME,
    CONFIG_FILENAME,
    ENV_CONFIG_PATH,
    ENV_XDG_CONFIG_HOME,
    AgentCodeConfig,
    ConfigError,
    find_config_path,
    load_config,
)

if TYPE_CHECKING:
    from pathlib import Path


def _valid_config_yaml() -> str:
    return """
phases:
  classification:
    url: http://vllm:8001/v1
    model_name: qwen3-7b
    api_key_env: VLLM_TOKEN
  dor:
    url: http://vllm:8001/v1
    model_name: qwen3-7b
  comprehension:
    url: http://vllm:8002/v1
    model_name: qwen3-32b
  planning:
    url: http://vllm:8002/v1
    model_name: qwen3-32b
  e2e_writing:
    url: http://vllm:8002/v1
    model_name: qwen3-32b
  implementation:
    url: http://vllm:8002/v1
    model_name: qwen3-32b
  review:
    url: http://vllm:8002/v1
    model_name: qwen3-32b
  summarizer:
    url: http://vllm:8001/v1
    model_name: qwen3-7b
template_path: /opt/agent-code/templates/python
mcp:
  context7:
    url: http://context7:9000
  duckduckgo:
    url: http://duckduckgo:9001
"""


def _write_config(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "config.yaml"
    target.write_text(body, encoding="utf-8")
    return target


def test_load_valid_config_returns_typed_object(tmp_path: Path) -> None:
    """A complete YAML config loads into AgentCodeConfig with all eight phases."""
    path = _write_config(tmp_path, _valid_config_yaml())

    config = load_config(path)

    assert isinstance(config, AgentCodeConfig)
    assert set(config.phases) == {
        "classification",
        "dor",
        "comprehension",
        "planning",
        "e2e_writing",
        "implementation",
        "review",
        "summarizer",
    }
    assert config.phases["classification"].api_key_env == "VLLM_TOKEN"
    assert config.mcp.context7.url == "http://context7:9000"
    assert config.loop.max_iterations_per_approach == 30  # default applied
    assert config.otel.exporter == "jsonl"  # default applied


def test_load_missing_file_raises_config_error(tmp_path: Path) -> None:
    """An explicit path that does not exist surfaces a clear ConfigError."""
    missing = tmp_path / "absent.yaml"
    with pytest.raises(ConfigError, match="No agent-code config found"):
        load_config(missing)


def test_load_invalid_yaml_raises_config_error(tmp_path: Path) -> None:
    """Malformed YAML produces a ConfigError mentioning the file."""
    path = _write_config(tmp_path, "phases: [unterminated")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(path)


def test_load_top_level_not_mapping_raises_config_error(tmp_path: Path) -> None:
    """A YAML scalar at the top level is rejected."""
    path = _write_config(tmp_path, "just-a-string")
    with pytest.raises(ConfigError, match="must be a YAML mapping"):
        load_config(path)


def test_load_missing_phase_raises_config_error(tmp_path: Path) -> None:
    """A config missing one of the eight required phase entries fails validation."""
    body = _valid_config_yaml().replace("  summarizer:\n    url: http://vllm:8001/v1\n    model_name: qwen3-7b\n", "")
    path = _write_config(tmp_path, body)
    with pytest.raises(ConfigError, match="missing required entries: summarizer"):
        load_config(path)


def test_load_unexpected_phase_raises_config_error(tmp_path: Path) -> None:
    """A config with an unknown phase entry is rejected."""
    body = _valid_config_yaml().replace(
        "  summarizer:\n    url: http://vllm:8001/v1\n    model_name: qwen3-7b\n",
        (
            "  summarizer:\n    url: http://vllm:8001/v1\n    model_name: qwen3-7b\n"
            "  bogus:\n    url: http://x:1/v1\n    model_name: bogus-1\n"
        ),
    )
    path = _write_config(tmp_path, body)
    with pytest.raises(ConfigError, match="unexpected entries: bogus"):
        load_config(path)


def test_load_url_without_scheme_raises_config_error(tmp_path: Path) -> None:
    """A phase URL without a scheme is rejected."""
    body = _valid_config_yaml().replace("http://vllm:8001/v1", "vllm-no-scheme", 1)
    path = _write_config(tmp_path, body)
    with pytest.raises(ConfigError, match="schema validation"):
        load_config(path)


def test_load_loop_overrides_apply(tmp_path: Path) -> None:
    """Custom loop limits in YAML override the defaults."""
    body = _valid_config_yaml() + "loop:\n  max_iterations_per_approach: 5\n  min_approaches: 2\n"
    path = _write_config(tmp_path, body)

    config = load_config(path)

    assert config.loop.max_iterations_per_approach == 5
    assert config.loop.min_approaches == 2
    assert config.loop.stagnation_threshold == 5  # untouched default


def test_find_config_path_explicit_existing(tmp_path: Path) -> None:
    """An explicit path that exists is returned verbatim."""
    target = _write_config(tmp_path, _valid_config_yaml())
    assert find_config_path(target) == target


def test_find_config_path_explicit_missing_returns_none(tmp_path: Path) -> None:
    """An explicit path that does not exist returns None (not the missing path)."""
    assert find_config_path(tmp_path / "nope.yaml") is None


def test_find_config_path_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`AGENT_CODE_CONFIG` env var is honored when no explicit path is given."""
    target = _write_config(tmp_path, _valid_config_yaml())
    monkeypatch.setenv(ENV_CONFIG_PATH, str(target))

    assert find_config_path() == target


def test_find_config_path_xdg_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls back to `$XDG_CONFIG_HOME/agent-code/config.yaml`."""
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.setenv(ENV_XDG_CONFIG_HOME, str(tmp_path))
    expected = tmp_path / APP_CONFIG_DIRNAME / CONFIG_FILENAME
    expected.parent.mkdir(parents=True)
    expected.write_text(_valid_config_yaml(), encoding="utf-8")

    assert find_config_path() == expected


def test_find_config_path_nothing_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When neither explicit nor env nor XDG default exists, returns None."""
    monkeypatch.delenv(ENV_CONFIG_PATH, raising=False)
    monkeypatch.setenv(ENV_XDG_CONFIG_HOME, str(tmp_path))

    assert find_config_path() is None
