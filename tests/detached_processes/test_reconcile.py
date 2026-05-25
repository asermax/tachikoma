"""Tests for the shared reconciler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.buffer.priority import Priority
from tachikoma.detached_processes.model import STOP_REASON_AGENT_STOPPED
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


# ---------------------------------------------------------------------------
# Agent-stopped suppression tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_stopped_suppresses_watcher_notification(repo, tmp_path, mock_bus):
    """AC1: Watcher notification suppressed when stop_reason='agent_stopped'."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(
        _make_record(
            record_id="rec-stopped",
            status="running",
            stop_reason=STOP_REASON_AGENT_STOPPED,
        )
    )
    (log_dir / "rec-stopped.exit").write_text("143\n")

    await reconcile_exit(
        "rec-stopped",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    updated = await repo.get("rec-stopped")
    assert updated is not None
    assert updated.status == "exited"
    assert updated.exit_code == 143

    mock_bus.dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_natural_exit_still_notifies(repo, tmp_path, mock_bus):
    """AC2: Natural exit (no stop_reason) dispatches notification."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-natural", status="running"))
    (log_dir / "rec-natural.exit").write_text("0\n")

    await reconcile_exit(
        "rec-natural",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    updated = await repo.get("rec-natural")
    assert updated is not None
    assert updated.status == "exited"

    mock_bus.dispatch.assert_called_once()


@pytest.mark.asyncio
async def test_agent_stopped_already_exited_is_noop(repo, tmp_path, mock_bus):
    """Idempotency: already-exited record with stop_reason is a no-op."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(
        _make_record(
            record_id="rec-exited-stopped",
            status="exited",
            exit_code=0,
            stop_reason=STOP_REASON_AGENT_STOPPED,
        )
    )

    await reconcile_exit(
        "rec-exited-stopped",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
    )

    mock_bus.dispatch.assert_not_called()


# ---------------------------------------------------------------------------
# OOM detection and cgroup cleanup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oom_detected_notification(repo, tmp_path, mock_bus):
    """OOM kill detected: notification includes OOM context with memory limit."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-test")
    await repo.create(
        _make_record(
            record_id="rec-oom",
            status="running",
            cgroup_path=cgroup_path,
            memory_limit=512 * 1024 * 1024,  # 512MB
        )
    )
    (log_dir / "rec-oom.exit").write_text("137\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=True),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup"),
    ):
        await reconcile_exit(
            "rec-oom",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
            dispatch_notification=True,
        )

    event = mock_bus.dispatch.call_args[0][0]
    assert "OOM" in event.prompt
    assert "512MB limit" in event.prompt
    assert event.severity == "error"
    assert event.priority == Priority.URGENT


@pytest.mark.asyncio
async def test_normal_sigkill_notification(repo, tmp_path, mock_bus):
    """Exit 137 without OOM: notification says 'killed by signal'."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-sigkill")
    await repo.create(
        _make_record(
            record_id="rec-sigkill",
            status="running",
            cgroup_path=cgroup_path,
            memory_limit=512 * 1024 * 1024,
        )
    )
    (log_dir / "rec-sigkill.exit").write_text("137\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=False),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup"),
    ):
        await reconcile_exit(
            "rec-sigkill",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
            dispatch_notification=True,
        )

    event = mock_bus.dispatch.call_args[0][0]
    assert "SIGKILL" in event.prompt
    assert "OOM" not in event.prompt


@pytest.mark.asyncio
async def test_normal_exit_no_oom_context(repo, tmp_path, mock_bus):
    """Exit code 0: standard notification, no OOM mention."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-ok")
    await repo.create(
        _make_record(
            record_id="rec-normal",
            status="running",
            cgroup_path=cgroup_path,
            memory_limit=256 * 1024 * 1024,
        )
    )
    (log_dir / "rec-normal.exit").write_text("0\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=False),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup"),
    ):
        await reconcile_exit(
            "rec-normal",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
            dispatch_notification=True,
        )

    event = mock_bus.dispatch.call_args[0][0]
    assert "exited with code 0" in event.prompt
    assert "OOM" not in event.prompt
    assert "SIGKILL" not in event.prompt


@pytest.mark.asyncio
async def test_cgroup_cleanup_called_on_exit(repo, tmp_path, mock_bus):
    """cleanup_cgroup is called when record has a cgroup_path."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-cleanup")
    await repo.create(
        _make_record(
            record_id="rec-clean",
            status="running",
            cgroup_path=cgroup_path,
        )
    )
    (log_dir / "rec-clean.exit").write_text("0\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=False),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup") as mock_cleanup,
    ):
        await reconcile_exit(
            "rec-clean",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
            dispatch_notification=True,
        )

    mock_cleanup.assert_called_once_with(cgroup_path)


@pytest.mark.asyncio
async def test_no_cgroup_reconcile_normal(repo, tmp_path, mock_bus):
    """Reconcile works normally when cgroup_path is None."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    await repo.create(_make_record(record_id="rec-no-cgroup", status="running"))
    (log_dir / "rec-no-cgroup.exit").write_text("0\n")

    await reconcile_exit(
        "rec-no-cgroup",
        repository=repo,
        bus=mock_bus,
        log_dir=log_dir,
        dispatch_notification=True,
    )

    updated = await repo.get("rec-no-cgroup")
    assert updated is not None
    assert updated.status == "exited"
    assert updated.exit_code == 0

    event = mock_bus.dispatch.call_args[0][0]
    assert "exited with code 0" in event.prompt


@pytest.mark.asyncio
async def test_oom_detected_without_memory_limit(repo, tmp_path, mock_bus):
    """OOM detected but no memory_limit stored: no limit string in notification."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-nolimit")
    await repo.create(
        _make_record(
            record_id="rec-oom-nolimit",
            status="running",
            cgroup_path=cgroup_path,
            memory_limit=None,
        )
    )
    (log_dir / "rec-oom-nolimit.exit").write_text("137\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=True),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup"),
    ):
        await reconcile_exit(
            "rec-oom-nolimit",
            repository=repo,
            bus=mock_bus,
            log_dir=log_dir,
            dispatch_notification=True,
        )

    event = mock_bus.dispatch.call_args[0][0]
    assert "OOM" in event.prompt
    assert "MB limit" not in event.prompt


@pytest.mark.asyncio
async def test_cleanup_cgroup_called_even_without_notification(repo, tmp_path):
    """Cgroup cleanup happens even when dispatch_notification=False."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    cgroup_path = str(tmp_path / "cgroup-silent")
    await repo.create(
        _make_record(
            record_id="rec-silent-cg",
            status="running",
            cgroup_path=cgroup_path,
        )
    )
    (log_dir / "rec-silent-cg.exit").write_text("0\n")

    with (
        patch("tachikoma.detached_processes.reconcile.check_oom_kill", return_value=False),
        patch("tachikoma.detached_processes.reconcile.cleanup_cgroup") as mock_cleanup,
    ):
        await reconcile_exit(
            "rec-silent-cg",
            repository=repo,
            bus=None,
            log_dir=log_dir,
            dispatch_notification=False,
        )

    mock_cleanup.assert_called_once_with(cgroup_path)
