"""Rollback marker lifecycle and version rollback execution.

Marker files in /tmp bridge state across os.execv restart boundaries (DES-011).
The pending marker is written before restart and read on next startup. If the
new version fails during bootstrap, the rollback notification marker carries
failure details to the recovered process. The restart notification marker
carries reason + version context across a `restart`-triggered execv so the
next run can announce that the agent is back online.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from bubus import EventBus
from loguru import logger

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import dispatch_notification
from tachikoma.updates.apply import PACKAGE_NAME, UPGRADE_TIMEOUT

if TYPE_CHECKING:
    from tachikoma.plugins.loader import LoadedPlugin
    from tachikoma.plugins.manager import PluginManager

_log = logger.bind(component="updates")

MARKER_PATH = Path(tempfile.gettempdir()) / "tachikoma-update-pending.json"
NOTIFICATION_PATH = Path(tempfile.gettempdir()) / "tachikoma-update-rollback.json"
RESTART_NOTIFICATION_PATH = Path(tempfile.gettempdir()) / "tachikoma-restart-notification.json"


@dataclass(frozen=True)
class RollbackMarker:
    previous_version: str
    target_version: str
    timestamp: str


@dataclass(frozen=True)
class RollbackNotification:
    previous_version: str
    failed_version: str
    error: str


def write_rollback_marker(previous_version: str, target_version: str) -> None:
    """Write the rollback marker before os.execv restart."""
    data = {
        "previous_version": previous_version,
        "target_version": target_version,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    MARKER_PATH.write_text(json.dumps(data))
    _log.debug(
        "Rollback marker written: {prev} -> {target}",
        prev=previous_version,
        target=target_version,
    )


def read_rollback_marker() -> RollbackMarker | None:
    """Read the rollback marker. Returns None if absent or malformed."""
    try:
        data = json.loads(MARKER_PATH.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning("Malformed rollback marker, ignoring: {err}", err=exc)
        return None

    try:
        return RollbackMarker(
            previous_version=data["previous_version"],
            target_version=data["target_version"],
            timestamp=data["timestamp"],
        )
    except KeyError as exc:
        _log.warning("Malformed rollback marker, ignoring: {err}", err=exc)
        return None


def clear_rollback_marker() -> None:
    """Remove the rollback marker file. Idempotent."""
    with contextlib.suppress(FileNotFoundError):
        MARKER_PATH.unlink()


def write_rollback_notification(previous_version: str, failed_version: str, error: str) -> None:
    """Write the rollback notification marker before restarting with old version."""
    data = {
        "previous_version": previous_version,
        "failed_version": failed_version,
        "error": error,
    }
    NOTIFICATION_PATH.write_text(json.dumps(data))


def read_rollback_notification() -> RollbackNotification | None:
    """Read the rollback notification marker. Returns None if absent or malformed."""
    try:
        data = json.loads(NOTIFICATION_PATH.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning("Malformed rollback notification, ignoring: {err}", err=exc)
        return None

    try:
        return RollbackNotification(
            previous_version=data["previous_version"],
            failed_version=data["failed_version"],
            error=data["error"],
        )
    except KeyError as exc:
        _log.warning("Malformed rollback notification, ignoring: {err}", err=exc)
        return None


def clear_rollback_notification() -> None:
    """Remove the rollback notification file. Idempotent."""
    with contextlib.suppress(FileNotFoundError):
        NOTIFICATION_PATH.unlink()


@dataclass(frozen=True)
class RestartNotification:
    reason: Literal["update", "manual"]
    previous_version: str | None
    new_version: str | None
    timestamp: str


def write_restart_notification(
    reason: Literal["update", "manual"],
    previous_version: str | None,
    new_version: str | None,
) -> None:
    """Write the restart notification marker before os.execv restart."""
    data = {
        "reason": reason,
        "previous_version": previous_version,
        "new_version": new_version,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    RESTART_NOTIFICATION_PATH.write_text(json.dumps(data))


def read_restart_notification() -> RestartNotification | None:
    """Read the restart notification marker. Returns None if absent or malformed."""
    try:
        data = json.loads(RESTART_NOTIFICATION_PATH.read_text())
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, TypeError) as exc:
        _log.warning("Malformed restart notification, ignoring: {err}", err=exc)
        return None

    try:
        reason = data["reason"]
        if reason not in ("update", "manual"):
            _log.warning("Invalid restart notification reason: {reason}", reason=reason)
            return None
        return RestartNotification(
            reason=reason,
            previous_version=data["previous_version"],
            new_version=data["new_version"],
            timestamp=data["timestamp"],
        )
    except KeyError as exc:
        _log.warning("Malformed restart notification, ignoring: {err}", err=exc)
        return None


def clear_restart_notification() -> None:
    """Remove the restart notification file. Idempotent."""
    with contextlib.suppress(FileNotFoundError):
        RESTART_NOTIFICATION_PATH.unlink()


def build_back_online_content(notification: RestartNotification) -> str:
    """Build the back-online notification message from restart marker data."""
    if (
        notification.reason == "update"
        and notification.previous_version
        and notification.new_version
    ):
        return (
            f"Tachikoma is back online after an update restart "
            f"(upgraded from {notification.previous_version} "
            f"to {notification.new_version})."
        )
    return (
        f"Tachikoma is back online after a "
        f"{notification.reason} restart."
    )


async def handle_restart_notification(
    bus: EventBus,
    restart_notification: RestartNotification | None,
    rollback_was_dispatched: bool,
    plugin_manager: PluginManager | None = None,
) -> None:
    """Process restart notification marker and plugin failures after startup.

    Consolidates restart back-online content and plugin load failure summaries
    into a single notification dispatch.

    Implements DES-011 consume-once: clears the marker unconditionally
    before dispatching any side effect.
    """
    # DES-011: clear marker unconditionally before side effects.
    if restart_notification is not None:
        clear_restart_notification()

    parts: list[str] = []

    if restart_notification is not None and not rollback_was_dispatched:
        parts.append(build_back_online_content(restart_notification))

    if plugin_manager is not None:
        failed = plugin_manager.failed_plugins()
        if failed:
            parts.append(format_plugin_failures(failed))

    if not parts:
        return

    has_restart = restart_notification is not None and not rollback_was_dispatched
    source = "Back Online" if has_restart else "Plugin Startup"

    await dispatch_notification(
        bus,
        source=source,
        content="\n\n".join(parts),
        severity="info",
        priority=Priority.URGENT,
        source_id="restart_notification",
    )


def format_plugin_failures(failed: list[LoadedPlugin]) -> str:
    """Build a summary of failed plugins for a startup notification."""
    lines = ["Plugin startup issues:"]
    for plugin in failed:
        diagnostic = plugin.diagnostic or "unknown error"
        lines.append(f"- {plugin.alias}: {diagnostic}")
    return "\n".join(lines)


def run_rollback(version: str) -> bool:
    """Install a specific version via uv tool install. Returns True on success."""
    try:
        proc = subprocess.run(
            ["uv", "tool", "install", f"{PACKAGE_NAME}=={version}"],
            capture_output=True,
            text=True,
            timeout=UPGRADE_TIMEOUT,
        )
    except FileNotFoundError:
        _log.error("uv not found during rollback")
        return False
    except subprocess.TimeoutExpired:
        _log.error("Rollback timed out after {timeout}s", timeout=UPGRADE_TIMEOUT)
        return False

    if proc.returncode != 0:
        _log.error(
            "Rollback failed (exit code {code}): {stderr}",
            code=proc.returncode,
            stderr=proc.stderr.strip(),
        )
        return False

    return True
