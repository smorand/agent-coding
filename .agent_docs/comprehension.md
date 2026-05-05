# Comprehension phase (FR-005)

> Synthesizes a markdown comprehension report from the ticket and the
> workspace's project documentation. The output is the input to the
> planning phase. One LLM round-trip; no tool-calling for the MVP.

## Files

- `src/phases/comprehension.py`: phase implementation. Public surface:
  - `ComprehensionPhase(*, llm_client=None, workspace=None, per_file_cap_bytes=16_000, total_cap_bytes=64_000, agent_docs_cap=8)`.
    Without an `llm_client` the phase logs and returns CONTINUE (skeleton
    behavior, used when the run has no `config.yaml`). With a client, it
    reads sources, calls the LLM, persists the report, and returns CONTINUE.
  - `ComprehensionReport(summary, sources, model, input_tokens, output_tokens, generated_at)`:
    persisted artifact.
  - `ComprehensionSource(label, path, bytes_used)`: one element of `sources`.
  - `COMPREHENSION_REPORT_FILENAME = "comprehension.json"`.

## Source selection

Each run includes:

| Source | Cap (per-file) | Always included? |
|---|---|---|
| Ticket file | `per_file_cap_bytes` | yes |
| Workspace `CLAUDE.md` | `per_file_cap_bytes` | when present |
| Workspace `.agent_docs/*.md` | `per_file_cap_bytes` | top `agent_docs_cap` files by keyword overlap |

Files are appended in priority order until `total_cap_bytes` is exhausted.

### Keyword scoring

`_extract_keywords` splits the ticket text into lowercased identifiers
(`[A-Za-z][A-Za-z0-9_-]{2,}`), drops a small stopword set
(`the`, `and`, `ticket`, `criteria`, …), de-dupes, and keeps the first 20.

`_select_agent_docs` then ranks `.agent_docs/*.md` candidates by how many
of those keywords appear in their stem, falling back to alphabetical
order on ties.

### Truncation

`_truncate(text, max_bytes)` returns `(excerpt, used_bytes)` and appends
`\n... [truncated]` when it has to clip. Bytes are UTF-8 encoded; the
slice respects character boundaries via `errors="ignore"`.

## LLM call

- System prompt: instructs the model to produce a markdown report under
  1200 words with the exact section headers `## Context`,
  `## Ticket understanding`, `## Relevant areas of the codebase`,
  `## Open questions`, `## Risks`. Empty sections must say `None.`.
- User prompt: a sequence of `### <Label>: <name>\n\n<excerpt>` blocks,
  one per source (Ticket, CLAUDE.md, then each selected agent doc).
- Errors: a `LlmError` becomes a `HALT_ERROR` outcome with the original
  message. The orchestrator exits with code 3.

## Persisted artifact

`.agent_work/<ticket-id>/comprehension.json`:

```json
{
  "summary": "## Context\n\n…",
  "model": "qwen3-32b",
  "input_tokens": 1240,
  "output_tokens": 410,
  "generated_at": "2026-05-05T07:32:54+00:00",
  "sources": [
    {"label": "ticket", "path": "/path/to/ticket.md", "bytes_used": 412},
    {"label": "claude_md", "path": "/repo/CLAUDE.md", "bytes_used": 5120},
    {"label": "agent_docs", "path": "/repo/.agent_docs/bootstrap.md", "bytes_used": 4096}
  ]
}
```

## Wiring

- `agent_code._build_pipeline_components` builds a `PhaseLlmFactory` from
  the parsed `AgentCodeConfig.phases` and passes
  `llm_factory.for_phase("comprehension")` to `ComprehensionPhase`.
- `_run_pipeline` closes the factory in `finally` so HTTP connections
  release after the run, regardless of outcome.
- The workspace is resolved automatically from
  `ctx.work_dir.parent.parent` (canonical
  `workspace/.agent_work/<ticket-id>/` layout). Tests can pass an
  explicit `workspace=` to override.

## What is NOT done by this PR

- **Tool-calling**: comprehension does not use the `ToolRegistry` for now.
  Adding `query_docs`, `grep`, etc. as tool-calling round-trips is a
  follow-up once the planning phase needs richer navigation.
- **Caching across runs**: each invocation makes a fresh LLM call; no
  on-disk cache keyed on (ticket hash, source hashes).

## Testing

- `tests/test_phases_comprehension.py` (17 tests):
  - Pure helpers: `_extract_keywords`, `_select_agent_docs`, `_truncate`.
  - Phase behavior: no-LLM path, full path with stub LLM, CLAUDE.md
    inclusion, agent-docs scoring, total-byte cap, missing-ticket
    tolerance, `HALT_ERROR` on `LlmError`.
- `tests/test_agent_code.py`: `stub_llm` fixture monkey-patches
  `OpenAICompatClient.complete` so the bootstrap E2E test (which now
  traverses comprehension) doesn't need a live model endpoint.
