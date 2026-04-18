"""Tests for the detached processes bootstrap hook."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.database import Database
from tachikoma.detached_processes.hooks import detached_processes_hook
from tachikoma.detached_processes.repository import ProcessRepository

from .conftest import _make_record


@pytest.mark.asyncio
async def test_hook_creates_log_dir_and_repo(tmp_path):
    db_path = tmp_path / "tachikoma.db"
    database = Database(db_path)
    await database.initialize()

    ctx = MagicMock()
    ctx.extras = {"database": database}
    ctx.settings_manager.settings.workspace.path = tmp_path

    await detached_processes_hook(ctx)

    assert "process_repository" in ctx.extras
    assert "detached_process_log_dir" in ctx.extras
    assert (tmp_path / ".tachikoma" / "detached-processes").is_dir()

    await database.close()


@pytest.mark.asyncio
async def test_hook_crash_recovery_marks_dead_running(tmp_path):
    db_path = tmp_path / "tachikoma.db"
    database = Database(db_path)
    await database.initialize()

    repo = ProcessRepository(database.session_factory)

    # Create a running record with a fake-dead PID
    await repo.create(_make_record(record_id="dead-proc", status="running", pid=999999999))

    ctx = MagicMock()
    ctx.extras = {"database": database}
    ctx.settings_manager.settings.workspace.path = tmp_path

    await detached_processes_hook(ctx)

    # The dead record should be reconciled to exited
    record = await repo.get("dead-proc")
    assert record is not None
    assert record.status == "exited"

    await database.close()


@pytest.mark.asyncio
async def test_hook_idempotent_when_no_records(tmp_path):
    db_path = tmp_path / "tachikoma.db"
    database = Database(db_path)
    await database.initialize()

    ctx = MagicMock()
    ctx.extras = {"database": database}
    ctx.settings_manager.settings.workspace.path = tmp_path

    # Running twice should not raise
    await detached_processes_hook(ctx)
    await detached_processes_hook(ctx)

    await database.close()
