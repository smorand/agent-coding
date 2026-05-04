# Orchestrator

> The state machine that drives a ticket through the seven-phase pipeline.
> Persists run state to `.agent_work/<ticket_id>/state.json` after every
> phase transition and supports crash-resume by skipping completed phases on
> re-invocation.

## Files

- `src/state.py`: `State`, `PhaseRecord`, `PhaseName`, `PhaseStatus`, `RunStatus` Pydantic models, plus `StateStore` (atomic read/write).
- `src/phases/`: sub-package with the `Phase` interface (`base.py`) and seven concrete classes (one per phase). At skeleton stage every phase logs and returns a `CONTINUE` outcome.
- `src/orchestrator.py`: `Orchestrator` class. Loads or creates the run state, executes phases, persists after each transition, returns the process exit code.
- `src/agent_code.py`: Typer `run` command that wires the CLI to the orchestrator.

## Pipeline order

The `phases.PIPELINE` tuple defines the canonical order:

1. `classification` (FR-003)
2. `dor` (FR-004)
3. `comprehension` (FR-005)
4. `planning` (FR-006)
5. `e2e_writing` (FR-007)
6. `implementation` (FR-008, FR-009, FR-010)
7. `review` (FR-011)

Each phase has a `PhaseName` constant in `state.PhaseName`. Adding a phase is a three-step change: add the enum value, create the file under `src/phases/`, append the instance to `PIPELINE`.

## Phase contract

Every phase subclasses `phases.base.Phase`:

```python
class Phase(ABC):
    name: PhaseName

    async def prepare(self, ctx: PhaseContext) -> None: ...
    @abstractmethod
    async def run(self, ctx: PhaseContext) -> PhaseOutcome: ...
    async def checkpoint(self, ctx: PhaseContext) -> None: ...
```

`prepare` and `checkpoint` are no-op by default; override only when the phase needs to acquire side resources (LSP server, MCP client) or write artifacts (`plan.md`, etc.).

`run` returns a `PhaseOutcome`. The `OutcomeKind` drives the orchestrator:

| OutcomeKind | Effect | Exit code |
|---|---|---|
| `CONTINUE` | Move to the next phase | (none) |
| `HALT_OK` | Clean stop, all good | 0 |
| `HALT_DOR_FAILED` | DoR rejected the ticket | 1 |
| `HALT_EXHAUSTED` | Implementation loop gave up | 2 |
| `HALT_ERROR` | Unrecoverable system error | 3 |

If `run` raises, the orchestrator marks the phase `FAILED`, persists, and re-raises so the CLI converts to exit 3.

## State model

```python
class State(BaseModel):
    ticket_id: str
    template_version: str
    started_at: datetime
    last_checkpoint_at: datetime
    current_phase: PhaseName
    phases: list[PhaseRecord]
    run_status: RunStatus = RunStatus.RUNNING
    exit_code: int | None = None
```

`phases` is a list of `PhaseRecord` (one per phase) with status, start, completion, and optional error fields. The orchestrator updates each record as it transitions between phases.

## Atomic persistence

`StateStore.save` writes to `state.json.tmp` then renames to `state.json`. The `tmp_path.replace(target)` call is atomic on POSIX (and reasonably so on Windows). A crash mid-write never leaves a corrupted `state.json`. After a successful save, no `.tmp` file remains.

`StateStore.save` updates `last_checkpoint_at = datetime.now(UTC)` before writing.

`StateStore.load` raises:

- `FileNotFoundError` if no state exists at the expected path.
- `ValueError` with `"invalid JSON"` for unparseable JSON.
- `ValueError` with `"schema mismatch"` for JSON that does not match the `State` schema.

## Resume semantics

When `Orchestrator.run` is called with a `ticket_id` that already has a `state.json`:

1. The store loads the state.
2. The orchestrator iterates phases in order.
3. Phases with status `COMPLETED` are skipped.
4. The first non-completed phase becomes the resume point.
5. Execution proceeds normally from there.

This means a crash anywhere in the pipeline (during a phase or between phases) leaves a recoverable state on disk. The next invocation picks up where the previous one stopped.

Open question (TBD-5 in the spec): mid-phase crashes that did not checkpoint do re-run that phase from scratch; phases must therefore be either idempotent or maintain their own intra-phase checkpoints when their work is expensive.

## CLI

```bash
make run ARGS='run path/to/ticket.md'
```

The `run` command resolves the workspace (default: current directory), derives the ticket id from the file stem (lowercased, non-slug chars become hyphens), reads `.template_version` from the workspace, instantiates the orchestrator, and exits with the orchestrator's exit code.

## Where the orchestrator does NOT live

- `tests/test_*.py`: never modified by the orchestrator after the e2e_writing phase commits them. The tool wrapper enforces this; the orchestrator does not need to.
- `vars/project-template/`: the orchestrator does not touch the template itself; the bootstrap phase consumes it (FR-014).

## Testing

- `tests/test_state.py`: round-trip, atomic write, corruption handling, `exists`.
- `tests/test_phases.py`: pipeline composition, name uniqueness, skeleton outcomes.
- `tests/test_orchestrator.py`: order, persistence, halt outcomes, resume, exception propagation.
- `tests/test_agent_code.py`: CLI smoke (missing ticket, end-to-end skeleton run, slug derivation).
