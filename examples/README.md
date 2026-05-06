# Example configurations

Two ready-to-use `config.yaml` files. Copy one to
`~/.config/agent-code/config.yaml` (or pass `--config <path>`) and adjust the
endpoints / model names to match your setup.

## `config.local.yaml`

Single OpenAI-compatible endpoint at `http://localhost:8000/v1` serving one
Qwen 3.6 27B (or comparable) model. Use this when you run vLLM / TGI /
llama.cpp on the same host.

```bash
# representative vLLM startup
vllm serve Qwen/Qwen3.6-27B-Instruct --served-model-name qwen3.6-27b --port 8000

make run ARGS='run path/to/ticket.md --config examples/config.local.yaml'
```

To shard work across two endpoints (a small model for the deterministic phases,
the heavy model for everything else), duplicate the phase blocks and switch
their `url` / `model_name`. The agent treats each phase as independent.

## `config.openrouter.yaml`

Hosted endpoints via OpenRouter. Demo tiering:

| Phase | Model | Slug | $/1M (in/out) | Why |
|---|---|---|---|---|
| `classification`, `dor` | Qwen 3.5 9B | `qwen/qwen3.5-9b` | 0.10 / 0.15 | deterministic, regex-friendly; 262k context, output-cheap so an output-heavy workload runs significantly cheaper than `qwen/qwen3-8b` despite the higher input rate |
| `comprehension` | Qwen 3.6 Flash | `qwen/qwen3.6-flash` | 0.25 / 1.50 | one round-trip, large 1M context |
| `planning`, `e2e_writing`, `implementation`, `review` | Qwen 3.6 27B | `qwen/qwen3.6-27b` | 0.32 / 3.20 | the decision-makers (cap for the demo) |
| `summarizer` | Qwen 3.6 Flash | `qwen/qwen3.6-flash` | 0.25 / 1.50 | fast compression of older iterations |

Set the API key once:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...

make run ARGS='run path/to/ticket.md --config examples/config.openrouter.yaml'
```

To stay strictly within a budget, swap any phase that doesn't need the heavy
model. `qwen/qwen3.6-35b-a3b` (0.15 / 1.00, MoE) is a reasonable middle
ground. Pricing snapshot: 2026-05; verify with `or-cli models qwen/<slug>`
before relying on a number.

## Common knobs

Both files keep the same `loop`, `mcp`, and `otel` sections. The defaults
match the spec (`max_iterations_per_approach: 30`, `stagnation_threshold: 5`,
`min_approaches: 3`, `wall_clock_seconds: 7200`). Tighten them when you want
a cheaper short-iteration run; loosen them on a hard ticket.

`mcp.context7.url` and `mcp.duckduckgo.url` point at `http://localhost:9000`
and `:9001` placeholders. The MVP comprehension does not call MCP yet, so
non-running servers don't break the pipeline; replace the URLs once you have
real instances.

The `template_path` field defaults to `./vars/project-template`, which only
works when you run from the repo root. Set it to an absolute path if you
install the agent globally and use it from another working directory.
