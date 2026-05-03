# Ticket Template (Canonical)

> Mirror of Appendix A of the agent-code MVP specification. Keep in sync.
> A ticket consumed by `agent-code` MUST follow this structure. The Definition
> of Ready phase rejects any ticket that fails the rules below.

## Filename and location

- Local file: a Markdown file with extension `.md`.
- Recommended location: `tickets/<slug>.md` at the project root.
- Remote: a URL to a GitHub Issue body or equivalent. The agent fetches the body and treats it as a local file.

## Required structure

Required sections are marked `(REQUIRED)`. Optional sections may be omitted entirely (omit the header too).

```markdown
---
id: <ticket-slug>
title: <one-line title, 5 to 80 chars>
created: <ISO-8601 date>
author: <name or handle>
labels: [<optional, comma-separated labels>]
---

# <ticket-title>

## Description (REQUIRED)

<At least 50 characters of meaningful prose describing the change being
requested, its motivation, and the user-visible outcome. Do not paste code or
schemas here; those go in dedicated sections below.>

## Acceptance Criteria (REQUIRED)

<At least one criterion. Each criterion is one bullet that begins with a stable
identifier (AC-1, AC-2, ...). Each criterion must be testable: it should be
possible to write at least one E2E test that observes a concrete outcome.>

- AC-1: <testable criterion>
- AC-2: <testable criterion>
- AC-3: <testable criterion>

## Out of Scope

<Optional. Bullet list of things deliberately excluded from this ticket. Helps
the agent avoid unsolicited refactoring.>

- <excluded item 1>
- <excluded item 2>

## Infrastructure

<Optional. Declarative list of runtime requirements that must already be
provisioned in the project for the implementation to be testable. The agent
matches each item against the project's docker-compose.yml, environment, or
other infra declarations and stops with a request to provision if any item is
missing.>

- requires: postgres 15
- requires: redis 7
- requires: env var STRIPE_API_KEY

## Examples

<Optional. Concrete examples of inputs and expected outputs. Code blocks allowed.>

```python
>>> calc.subtract(5, 3)
2
```

## Notes

<Optional. Free-form notes for the agent: pointers to related code, prior art,
external references. Not a specification, just hints.>
```

## Field validation rules (used by the DoR check)

| Rule | Condition for `READY` |
|---|---|
| Frontmatter parses as valid YAML | Required |
| Frontmatter contains `id` (non-empty slug, matches `^[a-z0-9][a-z0-9-]{2,63}$`) | Required |
| Frontmatter contains `title` (5 to 80 chars, non-empty) | Required |
| `## Description` section exists | Required |
| Description body is at least 50 non-whitespace characters | Required |
| `## Acceptance Criteria` section exists | Required |
| Acceptance Criteria contains at least one bullet matching `^- AC-\d+: .+$` | Required |
| Each acceptance criterion bullet has at least 10 non-whitespace chars after `AC-N:` | Required |
| If `## Infrastructure` section exists, each `requires:` line has a non-empty value | Required |
| File extension is `.md` | Required |
| File parses as Markdown without errors | Required |

## DoR comment format (when NOT_READY)

When DoR fails, the agent appends or posts this comment using this exact format:

```markdown
<!-- agent-code DoR report; DO NOT EDIT below this line -->

## DoR Report (agent-code)

**Status**: NOT_READY
**Generated at**: <ISO-8601 timestamp>
**Agent version**: <semver>

### Missing or insufficient fields

- **<field name>**: <one-line explanation of what is missing or insufficient>
- ...

### How to proceed

Edit this ticket to address the points above, then re-trigger `agent-code`.

<!-- end agent-code DoR report -->
```

## Example: a READY ticket

See `tickets/example-ready.md` (shipped with the project template).

## Example: a NOT_READY ticket and resulting DoR comment

A ticket that omits the Acceptance Criteria section produces a DoR comment with the entry:

```
- **Acceptance Criteria**: section is missing. At least one bullet of the form `- AC-N: <criterion>` is required.
```
