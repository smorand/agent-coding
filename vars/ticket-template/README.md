# Ticket Template

> Canonical user story Markdown structure consumed by `agent-code`. The
> Definition of Ready (DoR) phase rejects any ticket missing required fields.

## Files

- `ticket-blank.md`: empty template; copy this into `tickets/<slug>.md` to start a new ticket.
- `ticket-example-ready.md`: a complete, READY example (the `add-subtract` ticket from the spec).
- `ticket-example-not-ready.md`: an incomplete example showing what triggers a DoR failure.

## How to use

1. In your project, create a `tickets/` directory if it does not exist.
2. Copy `ticket-blank.md` to `tickets/<your-slug>.md`.
3. Fill in every required section (frontmatter `id`, `title`; body `## Description` with at least 50 chars; `## Acceptance Criteria` with at least one bullet `- AC-1: ...`).
4. Run `agent-code tickets/<your-slug>.md` from the project root.

## Required fields summary

| Field | Required | Rule |
|---|---|---|
| Frontmatter `id` | yes | slug, matches `^[a-z0-9][a-z0-9-]{2,63}$` |
| Frontmatter `title` | yes | 5 to 80 chars |
| `## Description` | yes | at least 50 non-whitespace chars in body |
| `## Acceptance Criteria` | yes | at least one bullet `- AC-N: <criterion>` |
| `## Out of Scope` | no | bullet list |
| `## Infrastructure` | no | each `requires:` line has a non-empty value |
| `## Examples` | no | code or text |
| `## Notes` | no | free-form |

For the complete validation rules and DoR comment format, see `.agent_docs/ticket-template.md` in the Project Reference Template.
