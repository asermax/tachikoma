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
from typing import Literal

from loguru import logger

from tachikoma.updates.apply import PACKAGE_NAME, UPGRADE_TIMEOUT

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
    rollback_marker_present: bool
    previous_version: str | None
    new_version: str | None
    timestamp: str


def write_restart_notification(
    reason: Literal["update", "manual"],
    rollback_marker_present: bool,
    previous_version: str | None,
    new_version: str | None,
) -> None:
    """Write the restart notification marker before os.execv restart."""
    data = {
        "reason": reason,
        "rollback_marker_present": rollback_marker_present,
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
            rollback_marker_present=data["rollback_marker_present"],
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
