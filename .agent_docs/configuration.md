# Configuration

> Reference for the agent-code `config.yaml` (FR-002). Every phase model
> endpoint, MCP server, OTel exporter, loop limit, and secrets denylist is
> declared here. The agent fails fast at startup if any required field is
> missing or malformed.

## Lookup order

The loader resolves the config file in this order, returning the first that exists:

1. The path passed via `--config <path>` on the CLI.
2. The `AGENT_CODE_CONFIG` environment variable.
3. `$XDG_CONFIG_HOME/agent-code/config.yaml` (or `~/.config/agent-code/config.yaml` if `XDG_CONFIG_HOME` is unset).

If none of these exist, `load_config` raises `ConfigError` with an actionable message. Callers (the CLI commands) translate this to exit code 3.

## Validating a config

```bash
agent-code config-show                          # uses lookup order
agent-code config-show --config path/to/cfg.yaml
```

Loads, validates, and echoes the parsed config as indented JSON. Useful to verify a config file before launching a run.

## Schema

```yaml
phases:                       # MANDATORY, exactly these eight entries
  classification: { ... }
  dor: { ... }
  comprehension: { ... }
  planning: { ... }
  e2e_writing: { ... }
  implementation: { ... }
  review: { ... }
  summarizer: { ... }

template_path: /opt/agent-code/templates/python   # MANDATORY, path to vars/project-template/

loop:                          # OPTIONAL, defaults applied per field
  max_iterations_per_approach: 30
  stagnation_threshold: 5
  min_approaches: 3
  wall_clock_seconds: 7200

secrets_denylist:              # OPTIONAL, defaults to ["*_KEY","*_TOKEN","*_SECRET","*_PASSWORD"]
  - "*_KEY"
  - "*_TOKEN"
  - "*_SECRET"
  - "*_PASSWORD"

mcp:                           # MANDATORY
  context7:
    url: http://context7:9000
  duckduckgo:
    url: http://duckduckgo:9001

otel:                          # OPTIONAL, defaults applied
  exporter: jsonl              # jsonl | otlp
  path: ".agent_work/{ticket_id}/traces.jsonl"
  endpoint: null               # required when exporter == otlp
```

## Per-phase entry

Each of the eight entries under `phases:` follows the same schema:

```yaml
classification:
  url: http://vllm:8001/v1     # MANDATORY, OpenAI-compatible base URL with scheme
  model_name: qwen3-7b         # MANDATORY
  api_key_env: VLLM_TOKEN      # OPTIONAL, name of an env var holding the bearer token
  temperature: 0.2             # OPTIONAL
  max_tokens: 2048             # OPTIONAL
```

The eight phases are: `classification`, `dor`, `comprehension`, `planning`, `e2e_writing`, `implementation`, `review`, `summarizer`. The first seven mirror the orchestrator's pipeline; `summarizer` is invoked from inside the implementation loop to compress old context (FR-010).

Two phases can point to the same URL and model: a small workhorse (e.g., `qwen3-7b` on port 8001) may serve `classification`, `dor`, and `summarizer`; a larger model (e.g., `qwen3-32b` on port 8002) serves the rest.

## Validation rules

| Rule | Failure message contains |
|---|---|
| `phases` missing one of the eight required entries | `missing required entries: <names>` |
| `phases` has an entry with an unknown name | `unexpected entries: <names>` |
| `phases.<name>.url` lacks a scheme (no `://`) | `url must include a scheme` |
| `loop.<field>` not strictly positive | Pydantic validation error |
| `otel.exporter` not in `{jsonl, otlp}` | Pydantic pattern validation error |
| YAML is malformed | `not valid YAML` |
| Top-level YAML value is not a mapping | `must be a YAML mapping` |
| File missing | `No agent-code config found` |

## Example: minimal valid config

The `config-show` command can validate this verbatim:

```yaml
phases:
  classification: { url: http://vllm:8001/v1, model_name: qwen3-7b }
  dor: { url: http://vllm:8001/v1, model_name: qwen3-7b }
  comprehension: { url: http://vllm:8002/v1, model_name: qwen3-32b }
  planning: { url: http://vllm:8002/v1, model_name: qwen3-32b }
  e2e_writing: { url: http://vllm:8002/v1, model_name: qwen3-32b }
  implementation: { url: http://vllm:8002/v1, model_name: qwen3-32b }
  review: { url: http://vllm:8002/v1, model_name: qwen3-32b }
  summarizer: { url: http://vllm:8001/v1, model_name: qwen3-7b }
template_path: /opt/agent-code/templates/python
mcp:
  context7: { url: http://context7:9000 }
  duckduckgo: { url: http://duckduckgo:9001 }
```

Defaults will be filled in for `loop`, `secrets_denylist`, and `otel`.

## Secrets and tracing

- `api_key_env` references an environment variable. The agent NEVER reads or logs the variable's value as part of its audit trail; it only fetches it at HTTP call time and passes it as the bearer token.
- `secrets_denylist` patterns (glob-style) are matched against env var NAMES; matching variables' values are scrubbed from `.agent_work/` artifacts before they are committed (FR-017).
- `otel.path` may use the `{ticket_id}` placeholder; it is substituted at run time per ticket.
