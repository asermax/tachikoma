"""Update subsystem: periodic checks, upgrade execution, and restart signaling."""

from tachikoma.updates.apply import run_upgrade
from tachikoma.updates.checker import update_checker_tick
from tachikoma.updates.events import RestartRequested
from tachikoma.updates.hooks import updates_hook
from tachikoma.updates.tools import create_update_tools_server

__all__ = [
    "RestartRequested",
    "create_update_tools_server",
    "run_upgrade",
    "update_checker_tick",
    "updates_hook",
]
