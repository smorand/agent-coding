# Reviewer phase (FR-011)

> Runs after the implementation loop. Reads the ticket, plan, full
> branch diff (vs the recorded E2E commit SHA when present), and
> working-tree status; asks the configured LLM for a structured
> verdict; persists `review.json` for the PR-creation step.

## Files

- `src/phases/review.py`:
  - `ReviewPhase(*, llm_client=None, workspace=None, git_runner=None)`
  - `ReviewVerdict(StrEnum)`: `APPROVE` | `REQUEST_CHANGES`
  - `ReviewConcern(path, line, severity, reason)`: severity is
    `"blocking"` or `"suggestion"`; line is integer-as-string or `"?"`
  - `ReviewReport(verdict, blocking, suggestions, summary, model, …)`:
    persisted artifact
  - `parse_review_response(text)` — pure helper exported for tests

## LLM contract

```
## VERDICT
APPROVE | REQUEST_CHANGES

## BLOCKING
- <path>:<line> - <reason>
or "None."

## SUGGESTIONS
- <path>:<line> - <reason>
or "None."

## SUMMARY
2-3 lines.
```

The parser tolerates free-form bullets that don't match
`<path>:<line> - <reason>` by recording them with empty path and
`line="?"`.

## Persisted artifact

`.agent_work/<ticket-id>/review.json`:
```json
{
  "verdict": "APPROVE",
  "summary": "…",
  "blocking": [],
  "suggestions": [{"path": "src/x.py", "line": "42", "severity": "suggestion", "reason": "…"}],
  "model": "qwen3-32b",
  "input_tokens": 1240,
  "output_tokens": 410,
  "generated_at": "…"
}
```

`State.review_verdict` is set to the verdict string; the PR-creation
phase reads it to decide draft vs. ready.

## Diff target

When `state.e2e_commit_sha` is set, the phase calls `git diff <sha>`
(diff from the E2E lock to HEAD). Otherwise, plain `git diff`.

## Halt conditions

- `LlmError` → `HALT_ERROR` (exit 3)
- Malformed response (missing section, unknown verdict, empty SUMMARY) → `HALT_ERROR`
- Otherwise → `CONTINUE` (the verdict is consumed downstream)

The MVP does NOT loop back to the implementation phase on
`REQUEST_CHANGES`. The PR-creation step opens a draft PR with the
`agent-impl-blocked` label so a human can take over.
