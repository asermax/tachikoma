"""Agent upgrade execution: editable detection, subprocess invocation, result reporting."""

from __future__ import annotations

import importlib.metadata
import json
import subprocess
from dataclasses import dataclass

from loguru import logger

_log = logger.bind(component="updates")

PACKAGE_NAME = "tachikoma-agent"
UPGRADE_TIMEOUT = 120
EDITABLE_ERROR = "editable install"


@dataclass(frozen=True)
class UpgradeResult:
    old_version: str
    new_version: str | None
    already_up_to_date: bool
    error: str | None
    changed: bool


def _is_editable_install() -> bool:
    """Detect whether the package is installed as an editable/development install.

    Reads ``direct_url.json`` per PEP 610. Returns False if the file is
    missing or malformed (fallback = assume non-editable).
    """
    try:
        raw = importlib.metadata.distribution(PACKAGE_NAME).read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        _log.warning("Package not found, assuming non-editable")
        return False
    except FileNotFoundError:
        return False

    if raw is None:
        return False

    try:
        data = json.loads(raw)
        return bool(data.get("dir_info", {}).get("editable", False))
    except (json.JSONDecodeError, AttributeError):
        _log.warning("Malformed direct_url.json, assuming non-editable")
        return False


def run_upgrade() -> UpgradeResult:
    """Run ``uv tool upgrade`` and return a structured result."""
    if _is_editable_install():
        return UpgradeResult(
            old_version="",
            new_version=None,
            already_up_to_date=False,
            error=EDITABLE_ERROR,
            changed=False,
        )

    try:
        old_version = importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        old_version = "unknown"

    try:
        proc = subprocess.run(
            ["uv", "tool", "upgrade", PACKAGE_NAME],
            capture_output=True,
            text=True,
            timeout=UPGRADE_TIMEOUT,
        )
    except FileNotFoundError:
        return UpgradeResult(
            old_version=old_version,
            new_version=None,
            already_up_to_date=False,
            error="uv not found — is it installed and on PATH?",
            changed=False,
        )
    except subprocess.TimeoutExpired:
        return UpgradeResult(
            old_version=old_version,
            new_version=None,
            already_up_to_date=False,
            error=f"Upgrade timed out after {UPGRADE_TIMEOUT}s",
            changed=False,
        )

    if proc.returncode != 0:
        return UpgradeResult(
            old_version=old_version,
            new_version=None,
            already_up_to_date=False,
            error=f"Upgrade failed (exit code {proc.returncode}):\n{proc.stderr.strip()}",
            changed=False,
        )

    try:
        new_version = importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        new_version = old_version

    if new_version == old_version:
        return UpgradeResult(
            old_version=old_version,
            new_version=new_version,
            already_up_to_date=True,
            error=None,
            changed=False,
        )

    return UpgradeResult(
        old_version=old_version,
        new_version=new_version,
        already_up_to_date=False,
        error=None,
        changed=True,
    )
