"""Tests for spawn, liveness, and termination helpers."""

import asyncio
import contextlib
import os
import signal
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from tachikoma.detached_processes.spawn import is_alive, spawn_process, terminate

from .conftest import _make_record

# ---------------------------------------------------------------------------
# is_alive tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_alive_fresh_process():
    """A freshly spawned short-lived process is alive immediately."""
    proc = await asyncio.create_subprocess_exec(
        "sleep",
        "5",
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        create_time = psutil.Process(proc.pid).create_time()
        record = _make_record(pid=proc.pid, process_create_time=create_time)
        assert is_alive(record) is True
    finally:
        proc.terminate()
        await proc.wait()


@pytest.mark.asyncio
async def test_is_alive_dead_process():
    """A dead PID returns False."""
    proc = await asyncio.create_subprocess_exec(
        "true",
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()

    record = _make_record(pid=proc.pid, process_create_time=999999.0)
    assert is_alive(record) is False


@pytest.mark.asyncio
async def test_is_alive_pid_reuse_mismatch():
    """Mismatched create_time returns False even if PID exists."""
    proc = await asyncio.create_subprocess_exec(
        "sleep",
        "5",
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    try:
        # Use a deliberately wrong create_time
        record = _make_record(pid=proc.pid, process_create_time=0.0)
        assert is_alive(record) is False
    finally:
        proc.terminate()
        await proc.wait()


def test_is_alive_no_such_process():
    """Non-existent PID returns False."""
    record = _make_record(pid=999999999)
    assert is_alive(record) is False


# ---------------------------------------------------------------------------
# spawn_process tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_happy_path(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    record = await spawn_process(
        name="test-sleep",
        command="sleep 5",
        cwd=None,
        env_overrides=None,
        log_dir=log_dir,
        repository=repo,
    )

    try:
        assert record.id
        assert record.name == "test-sleep"
        assert record.command == "sleep 5"
        assert record.status == "running"
        assert record.pid > 0
        assert record.log_path.endswith(".log")
        assert Path(record.log_path).exists()

        # Verify process is actually running
        assert is_alive(record) is True
    finally:
        # Clean up
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_validates_name(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(ValueError, match="name"):
        await spawn_process(
            name="  ",
            command="echo hi",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
        )


@pytest.mark.asyncio
async def test_spawn_validates_command(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(ValueError, match="command"):
        await spawn_process(
            name="test",
            command="  ",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
        )


@pytest.mark.asyncio
async def test_spawn_validates_cwd(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with pytest.raises(FileNotFoundError):
        await spawn_process(
            name="test",
            command="echo hi",
            cwd=Path("/nonexistent/path"),
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
        )


@pytest.mark.asyncio
async def test_spawn_shell_features(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    record = await spawn_process(
        name="pipe-test",
        command="echo hello && echo world",
        cwd=None,
        env_overrides=None,
        log_dir=log_dir,
        repository=repo,
    )

    try:
        assert record.status == "running"
        # Wait a bit for output
        await asyncio.sleep(0.5)

        # Check the log file captured output
        log_path = Path(record.log_path)
        if log_path.exists():
            content = log_path.read_text()
            assert "hello" in content
            assert "world" in content
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_env_overrides(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    record = await spawn_process(
        name="env-test",
        command="echo $MY_TEST_VAR",
        cwd=None,
        env_overrides={"MY_TEST_VAR": "hello_from_test"},
        log_dir=log_dir,
        repository=repo,
    )

    try:
        await asyncio.sleep(0.5)
        log_path = Path(record.log_path)
        if log_path.exists():
            content = log_path.read_text()
            assert "hello_from_test" in content
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


# ---------------------------------------------------------------------------
# terminate tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_sends_sigterm(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    record = await spawn_process(
        name="term-test",
        command="sleep 60",
        cwd=None,
        env_overrides=None,
        log_dir=log_dir,
        repository=repo,
    )

    await terminate(record, timeout=5)
    await asyncio.sleep(0.2)
    assert is_alive(record) is False


@pytest.mark.asyncio
async def test_terminate_escalates_to_sigkill(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    # Trap SIGTERM so the process ignores it
    record = await spawn_process(
        name="trap-test",
        command="trap '' TERM; sleep 60",
        cwd=None,
        env_overrides=None,
        log_dir=log_dir,
        repository=repo,
    )

    await terminate(record, sig=signal.SIGTERM, timeout=2)
    await asyncio.sleep(0.2)
    assert is_alive(record) is False


@pytest.mark.asyncio
async def test_terminate_timeout_zero(tmp_path, repo):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    record = await spawn_process(
        name="no-wait-test",
        command="sleep 60",
        cwd=None,
        env_overrides=None,
        log_dir=log_dir,
        repository=repo,
    )

    try:
        await terminate(record, timeout=0)

        # Process may or may not be dead yet — timeout=0 returns immediately
        # Just verify no exception was raised
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_terminate_already_dead(tmp_path, repo):
    """Terminating an already-dead record is a no-op."""
    record = _make_record(pid=999999999, process_create_time=0.0)
    await terminate(record)  # Should not raise


# ---------------------------------------------------------------------------
# spawn_process with cgroup tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_with_cgroup_creates_and_assigns(tmp_path, repo):
    """Cgroup is created and PID assigned when both params provided."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with (
        patch("tachikoma.detached_processes.spawn.cgroup_manager") as mock_cg,
    ):
        mock_cg.create_process_cgroup.return_value = "/sys/fs/cgroup/tachikoma-test"
        mock_cg.assign_pid.return_value = True

        record = await spawn_process(
            name="cg-test",
            command="sleep 5",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
            memory_limit_bytes=512 * 1024 * 1024,
            cgroup_parent_path="/sys/fs/cgroup",
        )

    try:
        assert record.cgroup_path == "/sys/fs/cgroup/tachikoma-test"
        assert record.memory_limit == 512 * 1024 * 1024
        mock_cg.create_process_cgroup.assert_called_once()
        mock_cg.assign_pid.assert_called_once_with("/sys/fs/cgroup/tachikoma-test", record.pid)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_cgroup_creation_fails_proceeds_without(tmp_path, repo):
    """Spawn succeeds without cgroup when creation fails."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with patch("tachikoma.detached_processes.spawn.cgroup_manager") as mock_cg:
        mock_cg.create_process_cgroup.return_value = None

        record = await spawn_process(
            name="cg-fail",
            command="sleep 5",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
            memory_limit_bytes=1024,
            cgroup_parent_path="/sys/fs/cgroup",
        )

    try:
        assert record.cgroup_path is None
        assert record.memory_limit is None
        mock_cg.assign_pid.assert_not_called()
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_assign_pid_fails_cleans_up(tmp_path, repo):
    """Cgroup is cleaned up when assign_pid fails."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with patch("tachikoma.detached_processes.spawn.cgroup_manager") as mock_cg:
        mock_cg.create_process_cgroup.return_value = "/sys/fs/cgroup/tachikoma-test"
        mock_cg.assign_pid.return_value = False

        record = await spawn_process(
            name="cg-assign-fail",
            command="sleep 5",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
            memory_limit_bytes=1024,
            cgroup_parent_path="/sys/fs/cgroup",
        )

    try:
        assert record.cgroup_path is None
        assert record.memory_limit is None
        mock_cg.cleanup_cgroup.assert_called_once_with("/sys/fs/cgroup/tachikoma-test")
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_no_cgroup_params_no_creation(tmp_path, repo):
    """No cgroup is created when memory_limit_bytes is None."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with patch("tachikoma.detached_processes.spawn.cgroup_manager") as mock_cg:
        record = await spawn_process(
            name="no-cg",
            command="sleep 5",
            cwd=None,
            env_overrides=None,
            log_dir=log_dir,
            repository=repo,
            memory_limit_bytes=None,
            cgroup_parent_path="/sys/fs/cgroup",
        )

    try:
        assert record.cgroup_path is None
        assert record.memory_limit is None
        mock_cg.create_process_cgroup.assert_not_called()
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)


@pytest.mark.asyncio
async def test_spawn_db_failure_cleans_up_cgroup(tmp_path, repo):
    """Cgroup is cleaned up when DB write fails after spawn."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    with (
        patch("tachikoma.detached_processes.spawn.cgroup_manager") as mock_cg,
        patch.object(repo, "create", side_effect=RuntimeError("db error")),
    ):
        mock_cg.create_process_cgroup.return_value = "/sys/fs/cgroup/tachikoma-test"
        mock_cg.assign_pid.return_value = True

        with pytest.raises(RuntimeError, match="db error"):
            await spawn_process(
                name="cg-db-fail",
                command="sleep 5",
                cwd=None,
                env_overrides=None,
                log_dir=log_dir,
                repository=repo,
                memory_limit_bytes=1024,
                cgroup_parent_path="/sys/fs/cgroup",
            )

    mock_cg.cleanup_cgroup.assert_called_once_with("/sys/fs/cgroup/tachikoma-test")
