# vars/

> Reference assets shipped with `agent-code`: project scaffolding template and
> ticket template. Both are consumed at runtime by the agent (project template
> at the bootstrap phase, ticket template at the DoR phase) and serve as the
> single source of truth for project conventions.

## Layout

```
vars/
├── README.md                 (this file)
├── ticket-template/
│   ├── README.md
│   ├── ticket-blank.md
│   ├── ticket-example-ready.md
│   └── ticket-example-not-ready.md
└── project-template/
    ├── .template_version
    ├── Makefile
    ├── pyproject.toml
    ├── .gitignore
    ├── .pre-commit-config.yaml
    ├── README.md
    ├── CLAUDE.md
    ├── LICENSE
    ├── Dockerfile
    ├── docker-compose.yml
    ├── .agent_docs/
    │   ├── python.md
    │   ├── makefile.md
    │   ├── testing.md
    │   ├── structure.md
    │   ├── tooling.md
    │   ├── ticket-template.md
    │   └── pr-template.md
    ├── src/
    │   ├── py.typed
    │   ├── __PROJECT_ENTRY__.py
    │   ├── config.py
    │   ├── logging_config.py
    │   └── tracing.py
    └── tests/
        ├── __init__.py
        ├── conftest.py
        ├── testdata/.gitkeep
        └── test___PROJECT_ENTRY__.py
```

## Self-contained design

The project template embeds **all** Python coding standards, Makefile reference, testing rules, structure rules, agent tooling reference, ticket template, and PR template inside its `CLAUDE.md` and `.agent_docs/`. A bootstrapped project can be operated by `agent-code` (or any LLM that can read Markdown) without any external "skill" file or model-side instruction. This is intentional: not every LLM runtime supports skills, and skill divergence over time is a known reliability risk.

## Placeholder substitution

The bootstrap phase substitutes the following placeholders when materializing the project template into a working directory:

| Placeholder | Source | Example |
|---|---|---|
| `__PROJECT_NAME__` | ticket frontmatter `id` (kebab-case) | `agent-code` |
| `__PROJECT_DESCRIPTION__` | first paragraph of ticket Description | `Autonomous coding agent...` |
| `__PROJECT_AUTHOR__` | ticket frontmatter `author` | `Sebastien MORAND` |
| `__PROJECT_AUTHOR_EMAIL__` | derived from git config or asked at install time | `seb.morand@gmail.com` |
| `__PROJECT_YEAR__` | current year | `2026` |
| `__PROJECT_ENTRY__` | snake_case of `__PROJECT_NAME__` | `agent_code` |
| `__PROJECT_PREFIX_UPPER__` | uppercase snake_case of `__PROJECT_NAME__`, with trailing underscore | `AGENT_CODE_` |

File names containing `__PROJECT_ENTRY__` are renamed at substitution time:

- `src/__PROJECT_ENTRY__.py` becomes `src/agent_code.py`
- `tests/test___PROJECT_ENTRY__.py` becomes `tests/test_agent_code.py`

Substitution is a literal string replace; no escape mechanism is provided since the placeholders use double underscores and never appear in normal Python or Markdown.

## Versioning

`vars/project-template/.template_version` is a semver string (currently `0.1.0`). The bootstrap phase records this version in:

- The initial commit message: `Bootstrap from agent-code Python template v<X.Y.Z>`.
- `.agent_work/<ticket-id>/state.json` under key `template_version`.

Future versions of `agent-code` may detect bootstrapped projects with stale templates and propose an upgrade. Auto-upgrading is out of MVP scope.

## How to validate the template

Until `agent-code` exists, the template can be validated manually:

```bash
mkdir /tmp/template-check && cd /tmp/template-check
git init
cp -R <repo>/vars/project-template/. .
# Substitute placeholders by hand:
sed -i.bak 's/__PROJECT_NAME__/hello/g; s/__PROJECT_ENTRY__/hello/g; s/__PROJECT_DESCRIPTION__/A test/g; s/__PROJECT_AUTHOR__/Test/g; s/__PROJECT_AUTHOR_EMAIL__/test@example.com/g; s/__PROJECT_YEAR__/2026/g; s/__PROJECT_PREFIX_UPPER__/HELLO_/g' Makefile pyproject.toml LICENSE Dockerfile README.md CLAUDE.md src/*.py tests/*.py vars/README.md
mv src/__PROJECT_ENTRY__.py src/hello.py
mv tests/test___PROJECT_ENTRY__.py tests/test_hello.py
find . -name '*.bak' -delete
uv sync
make check
```

If `make check` is green on the substituted template, the scaffolding is healthy.
