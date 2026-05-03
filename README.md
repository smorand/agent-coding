# agent-coding

Specification and (eventually) implementation of `agent-code`, an autonomous coding agent that takes a structured user story as input and produces a Pull Request as output.

## Status

**Specification phase.** No implementation yet. See `specs/` for the current MVP spec.

## Design principles

1. **Safety over speed.** The agent is designed to be slow and correct, not fast and approximate. It must work on on-premise mid-class open-weight models (Qwen 3 32B class), with no dependency on large proprietary models.
2. **Multi-model orchestration.** Each phase of the pipeline is served by an appropriately sized model declared in a configuration file.
3. **Test-first, anti-cheat.** End-to-end tests are written in an isolated phase before any implementation. Tests are locked read-only during the implementation loop. The Pull Request gate requires 100% of E2E tests passing, non-negotiable.
4. **Self-contained projects.** All coding standards, project conventions, and toolchain instructions live in the project's own `CLAUDE.md` and `.agent_docs/`, populated from a canonical Project Reference Template at bootstrap. The agent itself carries no language-specific knowledge.
5. **Auditable.** Every step of every run is persisted to `.agent_work/<ticket-id>/` and committed to the feature branch as a single audit trail artifact.

## Pipeline

```
ticket -> classify -> DoR check -> comprehend -> plan -> write E2E (locked) ->
implement loop (multi-approach) -> review (fresh context) -> open PR
```

## Scope of MVP

- Python projects only.
- Manual CLI invocation (`agent-code <ticket>`); event-driven invocation deferred to v2.
- Greenfield or template-bootstrapped projects only; no legacy support.

## Repository layout

```
agent-coding/
├── README.md           (this file)
├── specs/              (specification documents)
└── vars/               (reference assets shipped with agent-code)
    ├── ticket-template/    (canonical user story Markdown)
    └── project-template/   (canonical Python project scaffolding,
                             with all coding rules embedded in CLAUDE.md
                             and .agent_docs/, no external skill needed)
```

## License

To be decided before public implementation begins.
