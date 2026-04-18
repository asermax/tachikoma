"""Tests for exit watchers."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import watchfiles

from tachikoma.detached_processes.watcher import (
    event_driven_watcher,
    polling_watcher,
)

from .conftest import _make_record


# ---------------------------------------------------------------------------
# Event-driven watcher tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_driven_detects_exit_file(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="ev-1", status="running"))
    mock_bus = AsyncMock()

    async def mock_awatch(path):
        exit_file = log_dir / "ev-1.exit"
        exit_file.write_text("0\n")
        yield [(watchfiles.Change.added, str(exit_file))]

    with patch("tachikoma.detached_processes.watcher.watchfiles.awatch", side_effect=mock_awatch):
        await event_driven_watcher(repo, mock_bus, log_dir)

    updated = await repo.get("ev-1")
    assert updated is not None
    assert updated.status == "exited"
    assert updated.exit_code == 0


@pytest.mark.asyncio
async def test_event_driven_ignores_non_exit_files(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="ev-ignore", status="running"))
    mock_bus = AsyncMock()

    async def mock_awatch(path):
        yield [(watchfiles.Change.modified, str(log_dir / "ev-ignore.log"))]

    with patch("tachikoma.detached_processes.watcher.watchfiles.awatch", side_effect=mock_awatch):
        await event_driven_watcher(repo, mock_bus, log_dir)

    record = await repo.get("ev-ignore")
    assert record is not None
    assert record.status == "running"


@pytest.mark.asyncio
async def test_event_driven_handles_missing_record(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mock_bus = AsyncMock()

    async def mock_awatch(path):
        exit_file = log_dir / "nonexistent.exit"
        exit_file.write_text("0\n")
        yield [(watchfiles.Change.added, str(exit_file))]

    with patch("tachikoma.detached_processes.watcher.watchfiles.awatch", side_effect=mock_awatch):
        await event_driven_watcher(repo, mock_bus, log_dir)


@pytest.mark.asyncio
async def test_event_driven_cancellation(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mock_bus = AsyncMock()

    # Use real awatch on the log_dir — it will block until cancelled
    task = asyncio.create_task(event_driven_watcher(repo, mock_bus, log_dir))
    await asyncio.sleep(0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Polling watcher tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_polling_detects_dead_process(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="poll-1", status="running"))
    mock_bus = AsyncMock()

    iteration = 0
    original_sleep = asyncio.sleep

    async def sleep_then_cancel(interval):
        nonlocal iteration
        iteration += 1
        if iteration >= 2:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with (
        patch("tachikoma.detached_processes.watcher.is_alive", return_value=False),
        patch("tachikoma.detached_processes.watcher.asyncio.sleep", side_effect=sleep_then_cancel),
    ):
        with pytest.raises(asyncio.CancelledError):
            await polling_watcher(repo, mock_bus, log_dir, interval=0.01)

    updated = await repo.get("poll-1")
    assert updated is not None
    assert updated.status == "exited"


@pytest.mark.asyncio
async def test_polling_skips_alive_processes(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="poll-alive", status="running"))
    mock_bus = AsyncMock()

    iteration = 0
    original_sleep = asyncio.sleep

    async def sleep_then_cancel(interval):
        nonlocal iteration
        iteration += 1
        if iteration >= 1:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with (
        patch("tachikoma.detached_processes.watcher.is_alive", return_value=True),
        patch("tachikoma.detached_processes.watcher.asyncio.sleep", side_effect=sleep_then_cancel),
    ):
        with pytest.raises(asyncio.CancelledError):
            await polling_watcher(repo, mock_bus, log_dir, interval=0.01)

    record = await repo.get("poll-alive")
    assert record is not None
    assert record.status == "running"


@pytest.mark.asyncio
async def test_polling_handles_empty_running_list(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    mock_bus = AsyncMock()

    iteration = 0
    original_sleep = asyncio.sleep

    async def sleep_then_cancel(interval):
        nonlocal iteration
        iteration += 1
        if iteration >= 1:
            raise asyncio.CancelledError()
        await original_sleep(0)

    with patch("tachikoma.detached_processes.watcher.asyncio.sleep", side_effect=sleep_then_cancel):
        with pytest.raises(asyncio.CancelledError):
            await polling_watcher(repo, mock_bus, log_dir, interval=0.01)
