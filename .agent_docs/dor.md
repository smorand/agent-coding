# Definition of Ready (DoR) Phase

> First concrete pipeline phase (FR-004). Validates a ticket Markdown file
> against the canonical template defined in `vars/ticket-template/`. Pure
> structural validation; no LLM call. On failure, appends the canonical DoR
> comment to the ticket file and halts the pipeline with exit 1.

## Files

- `src/phases/dor_validator.py`: pure validation logic, no I/O beyond reading the ticket file. Returns a `DorReport` with status (`READY` / `NOT_READY`) and a tuple of `FieldIssue` per problem.
- `src/phases/dor.py`: `DorPhase` (replaces the prior stub). Reads the ticket via `asyncio.to_thread`, runs the validator, persists `dor_report.json` under `.agent_work/<ticket_id>/`, appends the canonical comment on failure, returns the orchestrator outcome.

## Validation rules (Appendix A.3 of the spec)

| Rule | Failure field | Failure message contains |
|---|---|---|
| File extension is `.md` | `file extension` | "must be a .md (Markdown) file" |
| YAML frontmatter present (between `---` lines) | `frontmatter` | "is missing" |
| Frontmatter parses as YAML | `frontmatter` | "YAML parse error" |
| Frontmatter is a mapping | `frontmatter` | "must be a YAML mapping" |
| `id` present | `id` | "is missing" |
| `id` matches `^[a-z0-9][a-z0-9-]{2,63}$` | `id` | "must match" |
| `title` present | `title` | "is missing" |
| `title` is 5 to 80 chars | `title` | "5 to 80 chars" |
| `## Description` section present | `Description` | "is missing" |
| Description body >= 50 non-whitespace chars | `Description` | "non-whitespace chars" |
| `## Acceptance Criteria` section present | `Acceptance Criteria` | "is missing" |
| At least one bullet `- AC-N: <criterion>` | `Acceptance Criteria` | "section is empty" |
| Each AC bullet >= 10 non-whitespace chars after `AC-N:` | `AC-<n>` | "fewer than 10 non-whitespace chars" |
| Each `- requires:` line in `## Infrastructure` has a value | `Infrastructure` | "empty value" |

The `(REQUIRED)` annotation in section titles (e.g., `## Description (REQUIRED)`) is stripped before matching, so the canonical template renders exactly as written in `vars/ticket-template/ticket-blank.md`.

## DoR comment format (Appendix A.4)

On NOT_READY, the phase appends this exact block to the ticket file:

```markdown
<!-- agent-code DoR report; DO NOT EDIT below this line -->

## DoR Report (agent-code)

**Status**: NOT_READY
**Generated at**: <ISO-8601 UTC>
**Agent version**: <semver>

### Missing or insufficient fields

- **<field>**: <one-line reason>
- ...

### How to proceed

Edit this ticket to address the points above, then re-trigger `agent-code`.

<!-- end agent-code DoR report -->
```

The block is appended (the original ticket content is preserved untouched). The begin/end HTML comments make it easy for tooling to find and replace the report on subsequent runs (a future enhancement; today the agent always appends).

## Persisted report

The phase writes `dor_report.json` to `.agent_work/<ticket_id>/` for every run (READY or NOT_READY). Schema:

```json
{
  "status": "READY",
  "generated_at": "2026-05-04T12:00:00+00:00",
  "issues": []
}
```

On NOT_READY, `issues` contains one entry per `FieldIssue`:

```json
{ "field": "Acceptance Criteria", "reason": "section is missing" }
```

## Orchestrator integration

```python
phase = DorPhase()  # uses default agent version 0.1.0
outcome = await phase.run(ctx)
# outcome.kind == OutcomeKind.CONTINUE on READY
# outcome.kind == OutcomeKind.HALT_DOR_FAILED on NOT_READY (orchestrator exits 1)
```

The DorPhase ignores `ctx.tools` (it does not need the registry) and `ctx.state.template_version`. It only consumes `ctx.ticket_path` and `ctx.work_dir`.

## Testing

- `tests/test_dor_validator.py` (18 tests): every validation rule, including parametrized cases for invalid ids and title lengths, plus two tests against the shipped `vars/ticket-template/ticket-example-ready.md` and `ticket-example-not-ready.md` fixtures.
- `tests/test_phases_dor.py` (4 tests): the phase wrapper. CONTINUE on READY, HALT_DOR_FAILED on NOT_READY with comment appended, work_dir auto-creation, agent version override.

## What is NOT covered yet

- **Remote ticket URLs**: today the validator only works on local files. The CLI takes a `Path`; supporting `https://github.com/.../issues/N` (FR-001 mentions URLs) requires a fetcher. Deferred.
- **Idempotency on re-run**: the comment is appended every time. A future enhancement will detect an existing block (between the begin/end markers) and replace it. Tracked as a TODO.
- **Posting comments to remote issues**: writes today are local file appends. The GitHub-issue path uses `gh issue comment` and lands with the `gh` wrapper PR.
- **Localized error messages**: all messages are English. Out of MVP scope.
