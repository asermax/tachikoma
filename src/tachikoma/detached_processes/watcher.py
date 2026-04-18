"""Exit watchers for detached processes.

Two complementary watcher tasks share a single reconciler:
- Event-driven watcher: uses watchfiles.awatch() for sub-second detection
  of .exit sidecar file writes.
- Polling watcher: periodic psutil liveness check every ~5s, catching
  cases where the wrapper itself was killed before writing the sidecar.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import watchfiles
from loguru import logger

from tachikoma.detached_processes.reconcile import reconcile_exit
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.spawn import is_alive

if TYPE_CHECKING:
    from bubus import EventBus

_log = logger.bind(component="detached_processes")

DETACHED_PROCESS_POLL_INTERVAL = 5.0


def _is_exit_file(change: watchfiles.Change, path: str) -> bool:
    return path.endswith(".exit")


async def event_driven_watcher(
    repository: ProcessRepository,
    bus: "EventBus",
    log_dir: Path,
) -> None:
    """Watch for .exit sidecar file creations using inotify/FSEvents.

    Filters events to .exit files at the OS layer, parses the record ID
    from the filename, and delegates to the shared reconciler.
    """
    try:
        async for changes in watchfiles.awatch(log_dir, watch_filter=_is_exit_file):
            for _change, path_str in changes:
                path = Path(path_str)

                if path.suffix != ".exit":
                    continue

                try:
                    await reconcile_exit(
                        path.stem,
                        repository=repository,
                        bus=bus,
                        log_dir=log_dir,
                    )
                except Exception:
                    _log.exception(
                        "Event-driven watcher: error processing {id}",
                        id=path.stem,
                    )
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.exception("Event-driven watcher: fatal error, stopping")


async def polling_watcher(
    repository: ProcessRepository,
    bus: "EventBus",
    log_dir: Path,
    interval: float = DETACHED_PROCESS_POLL_INTERVAL,
) -> None:
    """Periodically check running records for liveness via psutil.

    Catches cases the event-driven watcher misses (wrapper killed
    before writing the sidecar). Per-record error isolation ensures
    one bad record doesn't stop the loop.
    """
    try:
        while True:
            await asyncio.sleep(interval)

            try:
                records = await repository.list_running()
            except Exception:
                _log.exception("Polling watcher: error listing running records")
                continue

            for record in records:
                try:
                    if not is_alive(record):
                        await reconcile_exit(
                            record.id,
                            repository=repository,
                            bus=bus,
                            log_dir=log_dir,
                        )
                except Exception:
                    _log.exception(
                        "Polling watcher: error checking record {id}",
                        id=record.id,
                    )
    except asyncio.CancelledError:
        raise
    except Exception:
        _log.exception("Polling watcher: fatal error, stopping")
