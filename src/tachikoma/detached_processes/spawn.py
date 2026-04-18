"""Spawn, liveness, and termination helpers for detached processes.

Provides the low-level OS interactions: spawning detached children,
checking liveness via psutil, and terminating process groups.
"""

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

from tachikoma.detached_processes.model import ProcessRecord
from tachikoma.detached_processes.repository import ProcessRepository

_log = logger.bind(component="detached_processes")


def is_alive(record: ProcessRecord) -> bool:
    """Check whether a process record's process is still alive.

    Uses psutil to verify both PID existence and creation-time match
    (PID-reuse protection per R12). Returns False on NoSuchProcess,
    AccessDenied, or creation-time mismatch.
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
) -> ProcessRecord:
    """Spawn a detached shell command and persist the record.

    Builds a wrapper that captures the exit code to a sidecar file,
    spawns via asyncio.create_subprocess_exec with start_new_session=True,
    and persists the record. On DB failure after successful spawn, kills
    the process group as cleanup.
    """
    # Validate inputs
    if not name.strip():
        raise ValueError("name must not be empty or whitespace")
    if not command.strip():
        raise ValueError("command must not be empty or whitespace")

    target_cwd = str(cwd or Path.cwd())

    if cwd is not None and (not cwd.exists() or not cwd.is_dir()):
        raise ValueError(f"cwd does not exist or is not a directory: {cwd}")

    # Validate log directory is writable
    log_dir.mkdir(parents=True, exist_ok=True)
    if not os.access(log_dir, os.W_OK):
        raise OSError(f"log directory is not writable: {log_dir}")

    record_id = str(uuid4())
    exit_path = log_dir / f"{record_id}.exit"
    log_path = log_dir / f"{record_id}.log"

    # Build wrapper: run command, then capture exit code to sidecar
    wrapper = f"{command}; echo $? > {shlex.quote(str(exit_path))}"

    # Merge environment
    env = {**os.environ, **(env_overrides or {})}

    # Spawn the process — parent's copy of the log fd is closed in finally
    # (the child receives its own descriptor via fork).
    log_fd = open(log_path, "ab")  # noqa: SIM115
    try:
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
    finally:
        log_fd.close()

    # Capture identity pair immediately — failure here means we can't enforce
    # PID-reuse protection on this record, which is worse than no record at all.
    pid = proc.pid
    try:
        process_create_time = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        _log.exception("Failed to capture create_time for pid={pid}, killing child", pid=pid)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        raise OSError(f"Failed to capture process identity for pid {pid}: {exc}") from exc

    # Persist the record
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
    )

    try:
        return await repository.create(record)
    except Exception:
        # DB write failed — kill the orphaned process group
        _log.exception("DB write failed after spawn, killing pid={pid}", pid=pid)
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
        # Process already dead
        return

    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        raise

    if timeout <= 0:
        return

    # Poll for exit
    elapsed = 0.0
    interval = 0.1
    while elapsed < timeout:
        if not is_alive(record):
            return
        await asyncio.sleep(interval)
        elapsed += interval

    # Escalate to SIGKILL — reuse the original pgid (process group outlives
    # individual members while any are still alive, and we polled above)
    _log.warning(
        "Process {pid} still alive after {timeout}s, sending SIGKILL",
        pid=record.pid,
        timeout=timeout,
    )
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return

    # Brief wait to confirm exit
    for _ in range(10):
        if not is_alive(record):
            return
        await asyncio.sleep(0.1)
