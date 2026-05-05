# Planning phase (FR-006)

> Reads the ticket and the persisted comprehension report, asks the
> configured LLM for a structured plan in three sections (PLAN, TODO,
> INFRA NEEDS), persists each as a separate markdown file under
> `.agent_work/<ticket-id>/`, and validates the declared infrastructure
> against the workspace's `docker-compose.yml`. Halts the pipeline (exit
> code 1) when any required service is not provisioned (E2E-025).

## Files

- `src/phases/planning.py`:
  - `PlanningPhase(*, llm_client=None, workspace=None)`. Without a
    client the phase logs and returns CONTINUE (skeleton fallback for
    runs without a config).
  - `PlanningArtifacts(plan, todo, infra_needs)`: parsed sections.
  - `PlanningReport(artifacts, model, input_tokens, output_tokens, generated_at)`:
    persisted metadata.
  - `InfraIssue(requirement, service, reason)`: one unsatisfied need.
  - Pure helpers (importable for testing): `parse_planning_response`,
    `parse_infra_needs`, `detect_service`, `find_compose_file`,
    `compose_declares_service`, `validate_infra`, `format_infra_comment`.

## Persisted artifacts

All four files live under `.agent_work/<ticket-id>/`:

| File | Content |
|---|---|
| `plan.md` | High-level approach, files to touch, expected diff size |
| `todo.md` | Ordered checklist (`- [ ] task`) for the implementation phase |
| `infra_needs.md` | Bullet list of required services / env vars / binaries, or `None.` |
| `planning.json` | Model name, input/output tokens, `generated_at` |

## Prompt

The system prompt mandates three sections in this exact order with
these exact headers:

```
## PLAN
## TODO
## INFRA NEEDS
```

`parse_planning_response` splits on those headers and rejects any
response that is missing one, has them out of order, or has empty
sections. Parser failure → `HALT_ERROR` (exit 3).

The user prompt includes the ticket text and, when available, the
`summary` field from `comprehension.json` produced by the previous
phase. Missing comprehension is tolerated (the planning prompt simply
omits that section).

## Infrastructure validation

`validate_infra(workspace, infra_needs_text)` walks the bullets in
`infra_needs.md`. For each requirement it tries to detect a token from
`KNOWN_SERVICES` (`postgres`, `redis`, `mysql`, `mongodb`, `kafka`,
`minio`, `clickhouse`, …). When a known service is mentioned:

- The validator looks for a `docker-compose.yml`, `docker-compose.yaml`,
  `compose.yml`, or `compose.yaml` in the workspace.
- `compose_declares_service` does a textual word-boundary match for the
  service name in that file (best-effort; no YAML parsing for the MVP).
- If the file is absent or the name is not found, the requirement
  becomes an `InfraIssue`.

Requirements that don't mention a known service (env vars, binaries,
free-form text) are not flagged. The bias is to trust the human; the
validator only rejects the cases the spec calls out (E2E-025).

## Halting behavior

When `validate_infra` returns one or more issues:

1. The phase appends a templated comment to the ticket file:
   ```
   <!-- agent-code planning report -->

   ## Infrastructure not provisioned

   - Infrastructure requirement 'Postgres 15' is not provisioned in this
     project. Please add a docker-compose service or update the ticket.
   ```
2. Returns `OutcomeKind.HALT_DOR_FAILED`, which maps to exit code 1.
   (The exit code is shared with the DoR halt: both are
   "ticket/environment is not ready for implementation"; the message
   field disambiguates.)

## Wiring

- `agent_code._build_pipeline_components` builds a `PhaseLlmFactory`
  from the parsed config and passes
  `llm_factory.for_phase("planning")` to `PlanningPhase`.
- `_run_pipeline` closes both factories (MCP + LLM) in `finally` so
  HTTP connections always release.
- `workspace` is resolved from `ctx.work_dir.parent.parent` (canonical
  `workspace/.agent_work/<ticket-id>/` layout). Tests pass an explicit
  `workspace=` to override.

## Testing

- `tests/test_phases_planning.py` (25 tests):
  - Parser: well-formed, missing section, out-of-order, empty content.
  - Bullet/service helpers: stopwords, case insensitivity, env-var
    pass-through, compose detection, missing compose.
  - `validate_infra`: empty needs, missing service, declared service,
    mixed list (only the missing one is flagged), unknown requirement
    types are ignored.
  - Phase: no-LLM noop, full path with stub LLM, persisted artifacts +
    metadata, comprehension-summary inclusion in prompt, missing
    comprehension tolerated, `HALT_DOR_FAILED` on infra failure with
    ticket comment, `HALT_ERROR` on `LlmError` and on parser failure.
- `tests/test_agent_code.py`: the `stub_llm` fixture now returns a
  planning-shaped response when the system prompt looks like the
  planning phase, otherwise a comprehension-shaped one.

## What is NOT done by this PR

- **Committing the artifacts to git**: spec says the plan is committed
  as part of the audit trail. The git wrapper isn't wired into phases
  yet; the artifacts land on disk only.
- **YAML-aware compose parsing**: the textual word-boundary match is
  good enough for the MVP and matches the spec's example. A real YAML
  parser can replace it without changing the public surface.
- **Env-var / binary checks**: only declared services are validated.
  Env vars and binaries are listed in `infra_needs.md` but not
  enforced — the human reads the file before the next agent run.
