"""Persisted state of an agent run.

The state of a run for one ticket is written atomically to
.agent_work/<ticket_id>/state.json after every phase transition. The atomic
write (temp file + rename) guarantees that a crash mid-write never leaves a
corrupted state.json on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"


class PhaseName(StrEnum):
    """Canonical phase identifiers, ordered as they execute in the pipeline."""

    CLASSIFICATION = "classification"
    DOR = "dor"
    COMPREHENSION = "comprehension"
    PLANNING = "planning"
    E2E_WRITING = "e2e_writing"
    IMPLEMENTATION = "implementation"
    REVIEW = "review"
    PR_CREATION = "pr_creation"


class PhaseStatus(StrEnum):
    """Lifecycle status of a single phase within a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(StrEnum):
    """Overall lifecycle status of a run."""

    RUNNING = "running"
    COMPLETED = "completed"
    DOR_FAILED = "dor_failed"
    EXHAUSTED = "exhausted"
    SYSTEM_ERROR = "system_error"


class PhaseRecord(BaseModel):
    """Per-phase record stored in the run's state."""

    name: PhaseName
    status: PhaseStatus = PhaseStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class State(BaseModel):
    """Persisted state of a single agent run.

    Serialized to JSON in .agent_work/<ticket_id>/state.json. The schema is
    forward-compatible: unknown fields on read are tolerated by Pydantic when
    `model_config` allows it; for now we keep the schema strict.
    """

    ticket_id: str
    template_version: str
    started_at: datetime
    last_checkpoint_at: datetime
    current_phase: PhaseName
    phases: list[PhaseRecord] = Field(default_factory=list)
    run_status: RunStatus = RunStatus.RUNNING
    exit_code: int | None = None
    e2e_commit_sha: str | None = None
    implementation_commit_sha: str | None = None
    review_verdict: str | None = None
    pr_url: str | None = None


class StateStore:
    """Read and write the run state atomically."""

    __slots__ = ("_path",)

    def __init__(self, work_dir: Path) -> None:
        self._path = work_dir / STATE_FILENAME

    @property
    def path(self) -> Path:
        """Absolute path of the state file."""
        return self._path

    def exists(self) -> bool:
        """Return True if a state file is present on disk."""
        return self._path.exists()

    async def load(self) -> State:
        """Load and validate the state from disk.

        Raises FileNotFoundError if the file is absent and ValueError if the
        file is present but corrupted (invalid JSON or schema mismatch).
        """
        return await asyncio.to_thread(self._load_sync)

    async def save(self, state: State) -> None:
        """Persist the state atomically: write to a temp file then rename.

        Updates `last_checkpoint_at` to the current UTC time before writing.
        """
        state.last_checkpoint_at = datetime.now(UTC)
        await asyncio.to_thread(self._save_sync, state)

    def _load_sync(self) -> State:
        try:
            payload = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            msg = f"State file {self._path} is corrupted: invalid JSON"
            raise ValueError(msg) from exc
        try:
            return State.model_validate(data)
        except Exception as exc:
            msg = f"State file {self._path} is corrupted: schema mismatch"
            raise ValueError(msg) from exc

    def _save_sync(self, state: State) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = state.model_dump_json(indent=2)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(self._path)
        logger.debug("State checkpointed to %s", self._path)
