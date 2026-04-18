"""Tests for spawn, liveness, and termination helpers."""

import asyncio
import signal
from pathlib import Path

import psutil
import pytest

from tachikoma.detached_processes.model import ProcessRecord, ProcessStatus
from tachikoma.detached_processes.spawn import is_alive, spawn_process, terminate

from .conftest import _make_record


# ---------------------------------------------------------------------------
# is_alive tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_alive_fresh_process():
    """A freshly spawned short-lived process is alive immediately."""
    proc = await asyncio.create_subprocess_exec(
        "sleep", "5",
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
        "sleep", "5",
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
        import os
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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

    with pytest.raises(ValueError, match="cwd"):
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
        import os
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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
        import os
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


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
        import os
        try:
            os.killpg(os.getpgid(record.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


@pytest.mark.asyncio
async def test_terminate_already_dead(tmp_path, repo):
    """Terminating an already-dead record is a no-op."""
    record = _make_record(pid=999999999, process_create_time=0.0)
    await terminate(record)  # Should not raise
