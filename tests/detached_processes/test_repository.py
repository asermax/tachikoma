"""Tests for ProcessRepository."""

from datetime import UTC, datetime

import pytest

from tachikoma.detached_processes.model import STOP_REASON_AGENT_STOPPED

from .conftest import _make_record


@pytest.mark.asyncio
async def test_create_and_get_round_trip(repo):
    record = _make_record(record_id="create-test")
    await repo.create(record)
    fetched = await repo.get("create-test")

    assert fetched is not None
    assert fetched.id == "create-test"
    assert fetched.name == "Test Process"
    assert fetched.command == "sleep 10"
    assert fetched.status == "running"
    assert fetched.exit_code is None


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(repo):
    result = await repo.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_running_filters_by_status(repo):
    await repo.create(_make_record(record_id="r1", status="running"))
    await repo.create(_make_record(record_id="r2", status="running"))
    await repo.create(_make_record(record_id="e1", status="exited", exit_code=0))

    running = await repo.list_running()
    assert len(running) == 2
    assert all(r.status == "running" for r in running)
    ids = {r.id for r in running}
    assert ids == {"r1", "r2"}


@pytest.mark.asyncio
async def test_list_exited_filters_by_status(repo):
    await repo.create(_make_record(record_id="r1", status="running"))
    await repo.create(_make_record(record_id="e1", status="exited", exit_code=0))

    exited = await repo.list_exited()
    assert len(exited) == 1
    assert exited[0].id == "e1"


@pytest.mark.asyncio
async def test_rename_updates_name(repo):
    await repo.create(_make_record(record_id="u1", status="running"))

    await repo.rename("u1", "New Name")

    updated = await repo.get("u1")
    assert updated is not None
    assert updated.name == "New Name"


@pytest.mark.asyncio
async def test_rename_nonexistent_is_noop(repo):
    await repo.rename("nonexistent", "Ghost")

    assert await repo.get("nonexistent") is None


@pytest.mark.asyncio
async def test_delete_existing(repo):
    await repo.create(_make_record(record_id="d1"))
    assert await repo.delete("d1") is True
    assert await repo.get("d1") is None


@pytest.mark.asyncio
async def test_delete_nonexistent(repo):
    assert await repo.delete("nonexistent") is False


@pytest.mark.asyncio
async def test_reconcile_to_exited_wins_race(repo):
    await repo.create(_make_record(record_id="rec1", status="running"))
    now = datetime.now(UTC)

    won = await repo.reconcile_to_exited("rec1", exited_at=now, exit_code=0)
    assert won is True

    record = await repo.get("rec1")
    assert record is not None
    assert record.status == "exited"
    assert record.exit_code == 0


@pytest.mark.asyncio
async def test_reconcile_to_exited_loses_race(repo):
    await repo.create(_make_record(record_id="rec2", status="running"))
    now = datetime.now(UTC)

    # First reconcile wins
    assert await repo.reconcile_to_exited("rec2", exited_at=now, exit_code=0) is True

    # Second reconcile loses — already exited
    assert await repo.reconcile_to_exited("rec2", exited_at=now, exit_code=1) is False

    # Original values preserved
    record = await repo.get("rec2")
    assert record is not None
    assert record.exit_code == 0


@pytest.mark.asyncio
async def test_error_wrapping_surfaces_cause(repo, monkeypatch):
    """Verify that repository methods wrap exceptions with ProcessRepositoryError."""

    async def _failing_create(record):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "create", _failing_create)

    with pytest.raises(RuntimeError, match="disk full"):
        await repo.create(_make_record())


@pytest.mark.asyncio
async def test_mark_stop_initiated_sets_flag(repo):
    await repo.create(_make_record(record_id="stop-test"))
    await repo.mark_stop_initiated("stop-test")
    record = await repo.get("stop-test")
    assert record is not None
    assert record.stop_reason == STOP_REASON_AGENT_STOPPED


@pytest.mark.asyncio
async def test_clear_stop_reason(repo):
    await repo.create(_make_record(record_id="clear-test", stop_reason=STOP_REASON_AGENT_STOPPED))
    await repo.clear_stop_reason("clear-test")
    record = await repo.get("clear-test")
    assert record is not None
    assert record.stop_reason is None
