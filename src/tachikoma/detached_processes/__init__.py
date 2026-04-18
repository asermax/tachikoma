"""Detached process supervision subsystem.

MCP tools for spawning, monitoring, and terminating OS-level shell commands
that survive Tachikoma's own process lifetime.
"""

from tachikoma.detached_processes.errors import ProcessRepositoryError
from tachikoma.detached_processes.hooks import detached_processes_hook
from tachikoma.detached_processes.log_io import read_tail, read_window
from tachikoma.detached_processes.model import (
    ProcessRecord,
    ProcessStatus,
    ProcessStatusType,
)
from tachikoma.detached_processes.reconcile import reconcile_exit
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.spawn import is_alive, spawn_process, terminate
from tachikoma.detached_processes.tools import create_detached_process_tools_server
from tachikoma.detached_processes.watcher import (
    DETACHED_PROCESS_POLL_INTERVAL,
    event_driven_watcher,
    polling_watcher,
)

__all__ = [
    "DETACHED_PROCESS_POLL_INTERVAL",
    "ProcessRecord",
    "ProcessRepository",
    "ProcessRepositoryError",
    "ProcessStatus",
    "ProcessStatusType",
    "create_detached_process_tools_server",
    "detached_processes_hook",
    "event_driven_watcher",
    "is_alive",
    "polling_watcher",
    "read_tail",
    "read_window",
    "reconcile_exit",
    "spawn_process",
    "terminate",
]
