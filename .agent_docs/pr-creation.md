# PR creation phase (FR-012, E2E-024, E2E-026)

> Final phase. Verifies the E2E commit lock, pushes the branch, opens
> the Pull Request via `gh pr create`, optionally comments back on the
> ticket. The PR body matches the canonical template literally.

## Files

- `src/phases/pr_creation.py`:
  - `PrCreationPhase(*, workspace=None, git_runner=None)`
  - `PrCreationReport(pr_url, is_blocked, is_draft, labels, title, generated_at)`
  - Pure helpers exported for tests: `_parse_acceptance_criteria`,
    `_collect_e2e_tests`, `_extract_pr_url`, `_detect_github_issue_ref`

## Sequence

1. **Skeleton fallback**: when `state.e2e_commit_sha` is None (the E2E
   writing phase did not run with an LLM), skip with `CONTINUE`.
2. **E2E-026**: `git cat-file -e <e2e_commit_sha>^{commit}`. If the
   recorded SHA is no longer in the repo, halt with `HALT_EXHAUSTED`
   and the canonical message: `"E2E commit was modified after lock; aborting"`.
3. Build template inputs:
   - title and one-line summary from ticket frontmatter (`title:` line)
   - acceptance criteria from `- AC-N: text` bullets in the ticket
   - approach text from `plan.md`
   - E2E test refs by walking `tests/test_*.py` and pairing each
     `def test_*` with the contiguous `# AC-N` comment block
     immediately above it (no comment → fallback to `AC-1`)
4. `build_pr_body` validates and renders the markdown.
5. Ensure the `agent-impl-blocked` label exists on the repo (idempotent;
   only when the run is blocked).
6. `git push -u origin HEAD`. Failure → `HALT_ERROR`.
7. `gh pr create --title <…> --body <…> [--draft] [--label …]`. Failure → `HALT_ERROR`.
8. Capture URL, store on `state.pr_url`, persist `pr_creation.json`.
9. If the ticket text contains a GitHub issues URL, post
   `gh issue comment <n> --body "agent-code opened a Pull Request: <url>"`.

## Blocked vs. happy path

- **Happy** (review verdict == `APPROVE`): non-draft PR, no label,
  ACs rendered as `[x]`, plain title.
- **Blocked** (any other verdict, or no verdict): draft PR with
  `agent-impl-blocked` label, ACs rendered as `[ ]`, title prefixed
  with `[agent-impl-blocked]`, body has `## Attempted Approaches` +
  `## Proposed Decomposition` instead of `## Approach`.

## Persisted artifact

`.agent_work/<ticket-id>/pr_creation.json`:
```json
{
  "pr_url": "https://github.com/o/r/pull/42",
  "is_blocked": false,
  "is_draft": false,
  "labels": [],
  "title": "Add subtract feature",
  "generated_at": "…"
}
```

## What is NOT done by this PR

- **No re-run of implementation on `REQUEST_CHANGES`**: the spec allows
  one implementation re-run with the reviewer's blocking concerns
  added to context. The MVP opens the PR as draft instead.
- **No second-review gate**: spec says a second `REQUEST_CHANGES`
  triggers exhaustion. Not yet wired.
- **No richer ticket-comment formatting**: the comment is a single-line
  link.
