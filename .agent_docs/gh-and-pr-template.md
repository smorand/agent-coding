# GitHub CLI wrappers + PR body builder

> Building blocks for the future PR-creation step (FR-012, E2E-024). Adds
> four `gh` Tool wrappers in `src/tools/gh.py` and a pure markdown
> generator in `src/phases/pr_template.py` that produces a body matching
> the canonical template documented in `.agent_docs/pr-template.md`.

## Files

- `src/tools/gh.py`:
  - `GhPrCreateTool` (`gh_pr_create`): `gh pr create --title <t> --body <b> [--draft] [--label <name>]* [--base <b>] [--head <h>]`. Returns the PR URL on stdout.
  - `GhPrCommentTool` (`gh_pr_comment`): `gh pr comment <pr_number> --body <body>`.
  - `GhIssueCommentTool` (`gh_issue_comment`): `gh issue comment <issue_number> --body <body>`.
  - `GhLabelEnsureTool` (`gh_label_ensure`): `gh label create <name> [--color X] [--description Y]`. Idempotent — `already exists` errors map to ok=True.
- `src/phases/pr_template.py`:
  - `build_pr_body(inputs, *, title)` returns a `PrTemplateOutputs(title, body, is_blocked, labels, draft)`.
  - `PrTemplateInputs(ticket_reference, summary, acceptance_criteria, approach, e2e_tests, notable_decisions, out_of_scope, attempted_approaches=(), proposed_decomposition="")`.
  - Value objects: `AcceptanceCriterion(label, text, passed)`, `TestReference(pytest_path, acs)`, `AttemptedApproach(name, why_failed)`.
  - `section_headers_in_order(blocked)` returns the canonical section headers (used by E2E-024 verification).

## Tool design

All four tools follow the existing `_BaseGitTool` pattern: workspace +
runner + binary, `argv` built explicitly (no shell), errors surface as
`ToolResult(ok=False)` with stderr in `error`. Argument validation
returns `ToolResult(ok=False)` before invoking the runner so callers
never spend a subprocess on garbage input.

`GhLabelEnsureTool` looks for `already exists` or `HTTP 422` markers in
the failure output; either one means the label is now present, so the
result becomes ok=True with an explanatory `output`.

## PR body shape

### Happy path

```
## User Story
<ticket_ref>; <summary>

## Acceptance Criteria
- [x] AC-1: <text>
- [x] AC-2: <text>

## Approach
<3-5 lines>

## E2E Tests
- `<pytest_path>` validates AC-1
- `<pytest_path>` validates AC-2

## Notable Decisions
<text>

## Out of Scope
<text>
```

Title: `<title>` (no prefix).
Labels: `()`.
Draft: `False`.

### Blocked path (impl loop exhausted)

When `attempted_approaches` is non-empty, `build_pr_body` switches mode:

- The `## Approach` section becomes `## Attempted Approaches` listing
  each attempt with its failure reason.
- A `## Proposed Decomposition` section is appended (validation
  enforces it is non-empty).
- Failed ACs render as `[ ]` instead of `[x]`.
- Title gets the prefix `[agent-impl-blocked] `.
- Labels include `agent-impl-blocked`.
- Draft: `True`.

### Validation

`build_pr_body` raises `ValueError` when:

- `ticket_reference`, `summary`, `notable_decisions`, or `out_of_scope` are blank.
- `acceptance_criteria` or `e2e_tests` are empty.
- Happy mode but `approach` is blank.
- Blocked mode but `proposed_decomposition` is blank.

This matches the spec's "no empty sections" rule.

## E2E-024 verification

The body is designed to satisfy the regex from E2E-024:

```python
re.compile(
    r"^## User Story\n.*\n## Acceptance Criteria\n.*\n## Approach\n.*"
    r"\n## E2E Tests\n.*\n## Notable Decisions\n.*\n## Out of Scope\n.*$",
    re.DOTALL,
)
```

For the blocked case, swap `## Approach` for `## Attempted Approaches`
and append `\n## Proposed Decomposition\n.*`.

## What is NOT done by this PR

- **No phase wiring**: the future review/PR-creation phase will call
  `build_pr_body` to produce the body, then `GhPrCreateTool` to open the
  PR. That phase is the next milestone.
- **No SHA verification before PR**: the e2e_writing phase records
  `state.e2e_commit_sha`; checking that it matches `git log` before
  opening the PR (E2E-026) lands with the PR-creation step.
- **No automatic label creation**: `GhLabelEnsureTool` exists; the
  caller decides when to invoke it (typically on first blocked-PR
  creation).

## Testing

- `tests/test_tools_gh.py` (14 tests): argv shape for each tool,
  `--draft` and `--label` propagation, `--base` / `--head`, missing /
  invalid arguments rejected before invoking the runner, `gh` failure
  surfaces with stderr, `GhLabelEnsureTool` idempotency.
- `tests/test_pr_template.py` (15 tests): happy/blocked regex match,
  metadata (title prefix, labels, draft), AC checkbox state, pytest
  path notation, attempted approaches rendering, decomposition
  inclusion, validation errors for every empty-section case, and
  `section_headers_in_order` for both modes.
