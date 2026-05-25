"""Shared reconciler for transitioning process records from running to exited.

Used by both watcher paths (event-driven and polling) and by lazy
reconciliation in MCP tool handlers. Idempotent via conditional UPDATE
so concurrent reconcilers converge to a single winner.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.buffer.priority import Priority
from tachikoma.detached_processes.cgroup_manager import (
    BYTES_PER_MB,
    check_oom_kill,
    cleanup_cgroup,
)
from tachikoma.detached_processes.model import STOP_REASON_AGENT_STOPPED
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.notifications import dispatch_notification as _dispatch_notification

if TYPE_CHECKING:
    from bubus import EventBus

_log = logger.bind(component="detached_processes")


async def reconcile_exit(
    record_id: str,
    *,
    repository: ProcessRepository,
    bus: "EventBus | None",
    log_dir: Path,
    dispatch_notification: bool = True,
) -> None:
    """Transition a running record to exited, optionally dispatching a notification.

    Precondition: if dispatch_notification is True, bus must not be None.

    Idempotent: re-fetches the record and no-ops if status != 'running'.
    Uses conditional UPDATE so concurrent reconcilers converge to one winner.
    """
    try:
        record = await repository.get(record_id)
        if record is None or record.status != "running":
            return

        exit_path = log_dir / f"{record.id}.exit"
        exit_code = await _read_exit_code(exit_path)

        won = await repository.reconcile_to_exited(
            record.id,
            exited_at=datetime.now(UTC),
            exit_code=exit_code,
        )

        # Check for OOM and clean up cgroup if present
        oom_detected: bool | None = None
        if record.cgroup_path is not None:
            oom_detected = check_oom_kill(record.cgroup_path)
            cleanup_cgroup(record.cgroup_path)

        if not won or not dispatch_notification or bus is None:
            return

        if record.stop_reason == STOP_REASON_AGENT_STOPPED:
            _log.debug(
                "Suppressing exit notification for agent-stopped process {id}",
                id=record_id,
            )
            return

        if exit_code == 0:
            severity = "info"
            priority = Priority.NORMAL
        else:
            severity = "error"
            priority = Priority.URGENT

        if exit_code == 137:
            if oom_detected is True:
                limit_str = (
                    f" ({record.memory_limit // BYTES_PER_MB}MB limit)"
                    if record.memory_limit
                    else ""
                )
                content = (
                    f"Process '{record.name}' (id: {record.id}) was killed "
                    f"(OOM — may have exceeded memory limit{limit_str})."
                )
            else:
                content = (
                    f"Process '{record.name}' (id: {record.id}) was killed by signal (SIGKILL)."
                )
        else:
            code_str = str(exit_code) if exit_code is not None else "unknown"
            content = f"Process '{record.name}' (id: {record.id}) exited with code {code_str}."

        await _dispatch_notification(
            bus,
            source=f"Detached process: {record.name}",
            content=content,
            severity=severity,
            priority=priority,
            source_id=record.id,
        )

    except Exception:
        _log.exception("Error reconciling process {id}", id=record_id)


async def _read_exit_code(exit_path: Path) -> int | None:
    """Read the exit code from the sidecar file.

    Retries once after 100ms to cover kernel buffer lag on the wrapper's write.
    """
    try:
        return int(exit_path.read_text().strip())
    except FileNotFoundError:
        await asyncio.sleep(0.1)
    except (ValueError, OSError):
        _log.warning("Failed to parse exit code from {path}", path=exit_path)
        return None

    try:
        return int(exit_path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None
