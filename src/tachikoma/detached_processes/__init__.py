"""Detached process supervision subsystem.

MCP tools for spawning, monitoring, and terminating OS-level shell commands
that survive Tachikoma's own process lifetime.
"""

from tachikoma.detached_processes.errors import ProcessRepositoryError
from tachikoma.detached_processes.hooks import detached_processes_hook
from tachikoma.detached_processes.model import ProcessRecord, ProcessStatus
from tachikoma.detached_processes.repository import ProcessRepository
from tachikoma.detached_processes.tools import create_detached_process_tools_server
from tachikoma.detached_processes.watcher import event_driven_watcher, polling_watcher

__all__ = [
    "ProcessRecord",
    "ProcessRepository",
    "ProcessRepositoryError",
    "ProcessStatus",
    "create_detached_process_tools_server",
    "detached_processes_hook",
    "event_driven_watcher",
    "polling_watcher",
]
