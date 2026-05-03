# Agent Tooling Reference

> Every tool the agent has access to in this project, with purpose, when to
> use it, and common pitfalls. The agent reads this on demand to decide
> which tool fits the task at hand.

## Read tools

| Tool | Purpose | When to prefer |
|---|---|---|
| `read_file(path, [start, end])` | Read the contents of a file, optionally a slice | Always over `cat` via shell |
| `list_dir(path)` | List entries in a directory | Always over `ls` via shell |
| `grep(pattern, [glob], [path])` | ripgrep search across the codebase | Default search tool. Use globs to scope (e.g., `--glob='*.py'`). |
| `find(name_pattern, [path])` | Locate files by name pattern | When you need a path but not the contents |
| `ast_grep(pattern, lang)` | Structural search using AST patterns | When grep is too noisy: matching function calls, class definitions, decorators, etc. |
| `lsp_definition(symbol)` | Jump to the definition of a symbol | After `grep` finds a usage, to navigate to the implementation |
| `lsp_references(symbol)` | Find all references to a symbol | When refactoring or estimating impact |
| `lsp_hover(symbol)` | Get type signature and docstring | Quickly verify a function signature |
| `git_log([path])` | Show commit history for a path | Understand why code is the way it is |
| `git_blame(path, line)` | Show who last touched a line | Find the commit that introduced a behavior |
| `read_url(url)` | Fetch the contents of a URL | External documentation, RFCs, blog posts |

## Documentation MCP tools

| Tool | Purpose |
|---|---|
| `query_docs(library)` (Context7 MCP) | Up-to-date documentation for a Python library or framework |
| `resolve_library_id(library)` (Context7 MCP) | Find the canonical Context7 ID for a library |
| `search_web(query)` (DuckDuckGo MCP) | General web search; use only when local docs and Context7 are insufficient |

Prefer Context7 over web search for library documentation. Web search is the last resort: results vary in quality and freshness.

## Edit tools

| Tool | Purpose | When to prefer |
|---|---|---|
| `edit_file(path, old_string, new_string)` | Replace exactly one occurrence of `old_string` with `new_string` | Default tool for modifying existing files. `old_string` MUST be unique in the file. |
| `write_file(path, content)` | Write or overwrite a file with `content` | New files, complete rewrites of small files (< 300 lines) |
| `apply_patch(diff)` | Apply a unified diff to one or more files | Multi-line refactors across several locations in the same file, or coordinated changes |
| `delete_file(path)` | Remove a file | When code is genuinely no longer needed |
| `move_file(src, dst)` | Rename or move a file | Refactors |

Pitfalls:

- `edit_file`: if `old_string` is not unique, the call fails. Add more surrounding context to disambiguate. Do not silently retry; understand why.
- `apply_patch`: line counts in hunk headers must be exact. The agent's wrapper validates the diff format before applying.
- `write_file`: do not use to perform a small edit on a large file; you risk dropping unrelated content. Use `edit_file`.

## Constrained zones (anti-cheat)

During the implementation phase of a user story, writes to `tests/test_*.py` are blocked. The wrapper rejects any `edit_file`, `write_file`, `apply_patch`, or `delete_file` targeting these paths and returns an error: `tests/ is read-only during implementation phase; modify production code instead`. Writes to `tests/conftest.py`, `tests/testdata/`, and other non-`test_*.py` paths under `tests/` remain allowed.

This rule exists to prevent the agent from gaming its success criteria. Do not attempt to bypass it via `run_shell` (the wrapper inspects the resulting git status). See `.agent_docs/testing.md` for the full rule.

## Shell tool

| Tool | Purpose |
|---|---|
| `run_shell(cmd, [timeout])` | Execute an arbitrary shell command in the project root |

Use `run_shell` when no dedicated tool fits. Prefer `make <target>` over invoking the underlying tools directly. Examples of legitimate `run_shell` uses:

- `make test`, `make check`, `make sync`, etc.
- `git status`, `git diff` (when the dedicated git tools do not cover the case).
- `tree -L 2 src/` for visual directory layout.
- `wc -l` to estimate file size.

Avoid `run_shell` for:

- Reading files (use `read_file`).
- Editing files (use `edit_file`, `write_file`, `apply_patch`).
- Searching (use `grep`).
- Modifying files under `tests/test_*.py` indirectly (the wrapper blocks the resulting commits anyway).

Long-running commands should be background-friendly and bounded by the timeout argument.

## Git tools

| Tool | Purpose |
|---|---|
| `git_status()` | Show working tree status |
| `git_diff([path])` | Show unstaged or staged diffs |
| `git_diff_cached()` | Staged diffs only |
| `git_add(paths)` | Stage paths |
| `git_commit(message)` | Commit staged changes |
| `git_log([path], [limit])` | Show commit history |
| `git_blame(path, line)` | Show last touch on a line |
| `git_branch_create(name)` | Create a new branch |
| `git_checkout(ref)` | Switch branches or restore files |
| `git_reset(target, mode)` | Reset HEAD to a target (`soft`, `mixed`, `hard`) |

Reset to a previous E2E commit is the standard recovery move when the implementation loop hits a regression or stagnation.

## GitHub CLI tools

| Tool | Purpose |
|---|---|
| `gh_pr_create(title, body, [draft], [labels])` | Open a Pull Request |
| `gh_pr_comment(pr_number, body)` | Add a comment to a PR |
| `gh_issue_comment(issue_number, body)` | Add a comment to an issue (used for ticket comments) |
| `gh_label_ensure(name, [color], [description])` | Create the label on the repo if missing |

Used in the final phase to open the PR and notify the ticket. The label `agent-impl-blocked` is created on first use if missing.

## Build and test orchestration

The agent does not invoke `uv`, `pytest`, `ruff`, `mypy`, or `bandit` directly. It always goes through `make`:

| Goal | Command |
|---|---|
| Install or refresh dependencies | `run_shell("make sync")` |
| Run tests | `run_shell("make test")` |
| Run tests with coverage | `run_shell("make test-cov")` |
| Run the full quality gate | `run_shell("make check")` |
| Run the application | `run_shell("make run ARGS='...'")` |
| Format the code | `run_shell("make format")` |
| Build the package | `run_shell("make build")` |
