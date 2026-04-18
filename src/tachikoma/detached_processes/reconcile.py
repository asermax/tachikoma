"""Shared reconciler for transitioning process records from running to exited.

Used by both watcher paths (event-driven and polling) and by lazy
reconciliation in MCP tool handlers. Idempotent via conditional UPDATE
so concurrent reconcilers converge to a single winner.
"""

from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import dispatch_notification as _dispatch_notification

if TYPE_CHECKING:
    from bubus import EventBus

from tachikoma.detached_processes.repository import ProcessRepository

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

    Sidecar file ({id}.exit) is read for the exit code when present.
    A single 100ms retry covers kernel buffer lag on the sidecar write.
    """
    assert bus is not None or not dispatch_notification, (
        "bus is required when dispatch_notification=True"
    )

    try:
        # Re-fetch to guard against stale data
        record = await repository.get(record_id)
        if record is None or record.status != "running":
            return

        # Try to read the exit code sidecar
        exit_path = log_dir / f"{record.id}.exit"
        exit_code = _read_exit_code(exit_path)

        # Conditional UPDATE — only the race winner proceeds
        won = await repository.reconcile_to_exited(
            record.id,
            exited_at=datetime.now(UTC),
            exit_code=exit_code,
        )

        if not won:
            return

        if dispatch_notification:
            assert bus is not None  # guaranteed by precondition

            if exit_code == 0:
                severity = "info"
                priority = Priority.NORMAL
            else:
                severity = "error"
                priority = Priority.URGENT

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


def _read_exit_code(exit_path: Path) -> int | None:
    """Read the exit code from the sidecar file.

    Performs a single 100ms retry if the file is not yet present,
    covering kernel buffer lag on the wrapper's write.
    """
    for attempt in range(2):
        try:
            content = exit_path.read_text().strip()
            return int(content)
        except FileNotFoundError:
            if attempt == 0:
                sleep(0.1)
                continue
            return None
        except (ValueError, OSError):
            _log.warning("Failed to parse exit code from {path}", path=exit_path)
            return None

    return None
