"""Tests for state persistence: round-trip, atomic write, corruption handling."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from state import (
    PhaseName,
    PhaseRecord,
    PhaseStatus,
    RunStatus,
    State,
    StateStore,
)

if TYPE_CHECKING:
    from pathlib import Path


def _sample_state(ticket_id: str = "demo") -> State:
    # Anchor in the past so `last_checkpoint_at` is observably bumped on save.
    now = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    return State(
        ticket_id=ticket_id,
        template_version="0.1.0",
        started_at=now,
        last_checkpoint_at=now,
        current_phase=PhaseName.CLASSIFICATION,
        phases=[PhaseRecord(name=name) for name in PhaseName],
    )


async def test_save_then_load_round_trip(tmp_path: Path) -> None:
    """A saved state reloads to an equal object."""
    store = StateStore(tmp_path)
    original = _sample_state()
    await store.save(original)

    reloaded = await store.load()

    assert reloaded.ticket_id == original.ticket_id
    assert reloaded.template_version == original.template_version
    assert reloaded.current_phase == original.current_phase
    assert len(reloaded.phases) == len(original.phases)
    assert all(record.status == PhaseStatus.PENDING for record in reloaded.phases)
    assert reloaded.run_status == RunStatus.RUNNING


async def test_save_creates_parent_directory(tmp_path: Path) -> None:
    """Save creates the work directory if it does not yet exist."""
    work_dir = tmp_path / "deep" / "nested"
    store = StateStore(work_dir)
    assert not work_dir.exists()

    await store.save(_sample_state())

    assert (work_dir / "state.json").exists()


async def test_save_is_atomic_no_temp_file_left(tmp_path: Path) -> None:
    """After a successful save, no .tmp file is left behind."""
    store = StateStore(tmp_path)
    await store.save(_sample_state())

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


async def test_save_updates_last_checkpoint_at(tmp_path: Path) -> None:
    """Saving refreshes `last_checkpoint_at` to a current UTC timestamp."""
    state = _sample_state()
    original_checkpoint = state.last_checkpoint_at
    store = StateStore(tmp_path)
    await store.save(state)

    assert state.last_checkpoint_at >= original_checkpoint
    assert state.last_checkpoint_at.tzinfo is not None


async def test_load_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    """Loading from an empty directory raises FileNotFoundError."""
    store = StateStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        await store.load()


async def test_load_corrupted_json_raises_value_error(tmp_path: Path) -> None:
    """Loading a state.json with invalid JSON raises ValueError with context."""
    (tmp_path / "state.json").write_text("{not valid json", encoding="utf-8")
    store = StateStore(tmp_path)
    with pytest.raises(ValueError, match="invalid JSON"):
        await store.load()


async def test_load_schema_mismatch_raises_value_error(tmp_path: Path) -> None:
    """Loading a state.json that does not match the schema raises ValueError."""
    (tmp_path / "state.json").write_text('{"ticket_id": "x"}', encoding="utf-8")
    store = StateStore(tmp_path)
    with pytest.raises(ValueError, match="schema mismatch"):
        await store.load()


async def test_exists_reflects_disk_state(tmp_path: Path) -> None:
    """`exists` returns False before save, True after."""
    store = StateStore(tmp_path)
    assert store.exists() is False

    await store.save(_sample_state())

    assert store.exists() is True
