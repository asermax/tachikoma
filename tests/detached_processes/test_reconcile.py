"""Tests for the shared reconciler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.buffer.priority import Priority
from tachikoma.detached_processes.reconcile import reconcile_exit

from .conftest import _make_record


@pytest.fixture
def mock_bus():
    return AsyncMock()


@pytest.mark.asyncio
async def test_sidecar_zero_dispatches_normal(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-ok", status="running"))

    # Write exit code sidecar
    (log_dir / "rec-ok.exit").write_text("0\n")

    await reconcile_exit(
        "rec-ok",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    # Record should be exited
    updated = await repo.get("rec-ok")
    assert updated is not None
    assert updated.status == "exited"
    assert updated.exit_code == 0

    # Notification dispatched with NORMAL/info
    mock_bus.dispatch.assert_called_once()
    event = mock_bus.dispatch.call_args[0][0]
    assert event.severity == "info"
    assert event.priority == Priority.NORMAL


@pytest.mark.asyncio
async def test_sidecar_nonzero_dispatches_urgent(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-fail", status="running"))

    (log_dir / "rec-fail.exit").write_text("1\n")

    await reconcile_exit(
        "rec-fail",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    updated = await repo.get("rec-fail")
    assert updated is not None
    assert updated.status == "exited"
    assert updated.exit_code == 1

    event = mock_bus.dispatch.call_args[0][0]
    assert event.severity == "error"
    assert event.priority == Priority.URGENT


@pytest.mark.asyncio
async def test_no_sidecar_dispatches_urgent(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-nosidecar", status="running"))

    await reconcile_exit(
        "rec-nosidecar",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    updated = await repo.get("rec-nosidecar")
    assert updated is not None
    assert updated.exit_code is None

    event = mock_bus.dispatch.call_args[0][0]
    assert event.priority == Priority.URGENT


@pytest.mark.asyncio
async def test_suppressed_notification(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-silent", status="running"))
    (log_dir / "rec-silent.exit").write_text("0\n")

    await reconcile_exit(
        "rec-silent",
        repository=repo,
        bus=None,
        log_dir=log_dir,
        dispatch_notification=False,
    )

    updated = await repo.get("rec-silent")
    assert updated is not None
    assert updated.status == "exited"

    mock_bus.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_lost_race_no_dispatch(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-race", status="running"))
    (log_dir / "rec-race.exit").write_text("0\n")

    # Pre-reconcile so the conditional update loses
    await repo.reconcile_to_exited("rec-race", exited_at=datetime.now(UTC), exit_code=0)

    await reconcile_exit(
        "rec-race",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
    )

    mock_bus.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_already_exited_is_noop(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-done", status="exited", exit_code=0))

    await reconcile_exit(
        "rec-done",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
    )

    mock_bus.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_exception_is_logged_not_raised(repo, tmp_path, mock_bus):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Create record that will cause an error in reconciliation
    await repo.create(_make_record(record_id="rec-error", status="running"))

    # Make the get call fail
    with patch.object(repo, "get", side_effect=RuntimeError("db broke")):
        # Should not raise — error is logged internally
        await reconcile_exit(
            "rec-error",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
        )
