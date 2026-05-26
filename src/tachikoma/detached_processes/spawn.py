"""Spawn, liveness, and termination helpers for detached processes."""

import asyncio
import contextlib
import os
import shlex
import signal
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psutil
from loguru import logger

from tachikoma.detached_processes import cgroup_manager
from tachikoma.detached_processes.model import ProcessRecord
from tachikoma.detached_processes.repository import ProcessRepository

_log = logger.bind(component="detached_processes")


def is_alive(record: ProcessRecord) -> bool:
    """Check whether a process record's process is still alive.

    Uses psutil to verify both PID existence and creation-time match
    (PID-reuse protection). Returns False on NoSuchProcess, AccessDenied,
    or creation-time mismatch.
    """
    try:
        proc = psutil.Process(record.pid)
        return proc.create_time() == record.process_create_time and proc.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


async def spawn_process(
    name: str,
    command: str,
    cwd: Path | None,
    env_overrides: dict[str, str] | None,
    log_dir: Path,
    repository: ProcessRepository,
    *,
    memory_limit_bytes: int | None = None,
    cgroup_parent_path: str | None = None,
) -> ProcessRecord:
    """Spawn a detached shell command and persist the record.

    Builds a wrapper that captures the exit code to a sidecar file,
    spawns via asyncio.create_subprocess_exec with start_new_session=True,
    and persists the record. On DB failure after successful spawn, kills
    the process group as cleanup.

    If memory_limit_bytes and cgroup_parent_path are both provided,
    creates a per-process cgroup with the specified memory limit.
    """
    if not name.strip():
        raise ValueError("name must not be empty or whitespace")
    if not command.strip():
        raise ValueError("command must not be empty or whitespace")

    target_cwd = str(cwd or Path.cwd())

    record_id = str(uuid4())
    exit_path = log_dir / f"{record_id}.exit"
    log_path = log_dir / f"{record_id}.log"

    # Wrapper runs the command then writes its exit code to a sidecar file so
    # the watcher can recover it even after the parent exits.
    wrapper = f"{command}; echo $? > {shlex.quote(str(exit_path))}"

    env = {**os.environ, **(env_overrides or {})}

    with log_path.open("ab") as log_fd:
        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            wrapper,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log_fd,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env=env,
            cwd=target_cwd,
        )

    # Capture (pid, create_time) immediately — without it we can't enforce
    # PID-reuse protection on this record, which is worse than no record at all.
    pid = proc.pid
    try:
        process_create_time = psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        # Fast-finishing processes may exit before we can read create_time.
        # No PID-reuse risk since the process is already dead.
        process_create_time = 0.0
    except psutil.AccessDenied as exc:
        _log.exception("Failed to capture create_time for pid={pid}, killing child", pid=pid)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        raise OSError(f"Failed to capture process identity for pid {pid}: {exc}") from exc

    # If cgroup is configured, create per-process cgroup and assign PID
    cgroup_path: str | None = None
    if cgroup_parent_path is not None and memory_limit_bytes is not None:
        cgroup_path = cgroup_manager.create_process_cgroup(
            cgroup_parent_path, record_id, memory_limit_bytes
        )
        if cgroup_path is not None and not cgroup_manager.assign_pid(cgroup_path, pid):
            _log.warning(
                "Failed to assign pid={pid} to cgroup, proceeding without limit",
                pid=pid,
            )
            cgroup_manager.cleanup_cgroup(cgroup_path)
            cgroup_path = None

    record = ProcessRecord(
        id=record_id,
        name=name,
        command=command,
        cwd=target_cwd,
        pid=pid,
        process_create_time=process_create_time,
        log_path=str(log_path),
        status="running",
        started_at=datetime.now(UTC),
        memory_limit=memory_limit_bytes if cgroup_path else None,
        cgroup_path=cgroup_path,
    )

    try:
        return await repository.create(record)
    except Exception:
        _log.exception("DB write failed after spawn, killing pid={pid}", pid=pid)
        if cgroup_path is not None:
            cgroup_manager.cleanup_cgroup(cgroup_path)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        raise


async def terminate(
    record: ProcessRecord,
    *,
    sig: int = signal.SIGTERM,
    timeout: float = 10.0,
) -> None:
    """Send a signal to the process group and wait for exit.

    Uses os.killpg to signal the whole process group (wrapper + children).
    Escalates to SIGKILL if the process is still alive after the timeout.
    timeout=0 returns immediately after signalling.
    """
    try:
        pgid = os.getpgid(record.pid)
    except (ProcessLookupError, PermissionError):
        return

    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        raise

    if timeout <= 0:
        return

    elapsed = 0.0
    interval = 0.1
    while elapsed < timeout:
        if not is_alive(record):
            return
        await asyncio.sleep(interval)
        elapsed += interval

    # Reuse the original pgid — the process group outlives individual members
    # while any are still alive, and we polled above.
    _log.warning(
        "Process {pid} still alive after {timeout}s, sending SIGKILL",
        pid=record.pid,
        timeout=timeout,
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return

    for _ in range(10):
        if not is_alive(record):
            return
        await asyncio.sleep(0.1)
