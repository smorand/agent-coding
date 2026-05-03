# Pull Request Description Template (Canonical)

> Every Pull Request opened by `agent-code` follows this template literally.
> The reviewer agent verifies it. Mirror of section 8.4 of the spec; keep in
> sync. The template makes humans and other agents review the PR fast.

## Template

```markdown
## User Story
[link or path to ticket]; <one-line summary>

## Acceptance Criteria
- [x] AC-1: <criterion>
- [x] AC-2: <criterion>
- [x] AC-3: <criterion>

## Approach
<3 to 5 lines: what was done, where in the codebase, why this way. Mention
notable structural choices (new module, changed abstraction, dependency added).>

## E2E Tests
- `tests/test_<feature>.py::test_<name1>` validates AC-1
- `tests/test_<feature>.py::test_<name2>` validates AC-2
- `tests/test_<feature>.py::test_<name3>` validates AC-3
<List every E2E test added or modified, with the AC it covers. Use the
pytest path::function notation.>

## Notable Decisions
<Choices the human reviewer should explicitly confirm at merge time. For
example: "Used dataclass over Pydantic model for X because Y", "Extracted Z
into utils/ because it became shared by 3 modules", "Skipped retry logic
because the upstream service has its own".>

## Out of Scope
<What was deliberately not done in this PR and why. Things deferred,
explicitly excluded by the ticket, or out of bounds. References to follow-up
tickets if any.>
```

## Mandatory rules

- Every section above MUST be present.
- Every section MUST have non-empty content. Empty sections are forbidden.
- Section order MUST match the template literally.
- Section headers MUST match the template character for character.
- E2E test references MUST use the `path::function` pytest notation, so reviewers can copy-paste them into `make test ARGS='-k <name>'`.
- Acceptance Criteria checkboxes are checked (`[x]`) only when the corresponding tests pass; otherwise the PR is opened as draft with the `agent-impl-blocked` label and unchecked boxes for the missing ones.

## When the implementation loop fails

If the loop exhausts without converging, the agent still opens a draft Pull Request, but with the following adjustments:

- Title is prefixed with `[agent-impl-blocked]`.
- Label `agent-impl-blocked` is applied.
- The PR is opened as a draft.
- Acceptance Criteria checkboxes are unchecked for all the criteria whose tests are still failing.
- The `## Approach` section becomes `## Attempted Approaches`, listing each approach the agent tried and why it failed.
- A new section `## Proposed Decomposition` is added, suggesting how the user story could be split into smaller sub-stories.

The original section structure is preserved otherwise, so reviewers see a familiar layout.
