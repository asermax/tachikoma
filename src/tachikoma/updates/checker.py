"""Update checker: PyPI version fetch, comparison, and scheduled notification."""

import asyncio
import importlib.metadata
import json
import urllib.request
from dataclasses import dataclass
from urllib.error import URLError

from bubus import EventBus
from loguru import logger
from packaging.version import Version

from tachikoma.app_state import AppStateRepository
from tachikoma.buffer.priority import Priority
from tachikoma.notifications import dispatch_notification

_log = logger.bind(component="updates")

PYPI_URL = "https://pypi.org/pypi/tachikoma-agent/json"
DEDUP_KEY = "updates.last_notified_version"


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str | None
    update_available: bool
    latest_is_prerelease: bool


def fetch_latest_version() -> str | None:
    """Fetch the latest version string from PyPI. Returns None on any error."""
    try:
        req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "TachikomaUpdateChecker"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except (URLError, json.JSONDecodeError, KeyError, OSError) as exc:
        _log.warning("Failed to fetch latest version from PyPI: {err}", err=exc)
        return None


def check_for_update() -> UpdateCheckResult:
    """Compare installed version against PyPI latest."""
    try:
        current_str = importlib.metadata.version("tachikoma-agent")
    except importlib.metadata.PackageNotFoundError:
        current_str = "0.0.0"

    latest_str = fetch_latest_version()

    if latest_str is None:
        return UpdateCheckResult(
            current_version=current_str,
            latest_version=None,
            update_available=False,
            latest_is_prerelease=False,
        )

    current = Version(current_str)
    latest = Version(latest_str)

    is_prerelease = latest.is_prerelease or latest.is_devrelease
    update_available = latest > current and not is_prerelease

    return UpdateCheckResult(
        current_version=current_str,
        latest_version=latest_str,
        update_available=update_available,
        latest_is_prerelease=is_prerelease,
    )


async def update_checker_tick(
    app_state_repo: AppStateRepository,
    bus: EventBus,
) -> None:
    """Scheduled tick: check for updates and notify if a new version is available."""
    result = await asyncio.to_thread(check_for_update)

    if not result.update_available or result.latest_version is None:
        _log.debug(
            "No update available: current={current}, latest={latest}",
            current=result.current_version,
            latest=result.latest_version,
        )
        return

    try:
        last_notified = await app_state_repo.get(DEDUP_KEY)
    except Exception as exc:
        _log.warning("Failed to read dedup state: {err}", err=exc)
        return

    if last_notified == result.latest_version:
        _log.debug(
            "Already notified for version {ver}",
            ver=result.latest_version,
        )
        return

    await dispatch_notification(
        bus,
        source="Update Checker",
        content=(
            f"A new version of tachikoma-agent is available: "
            f"{result.current_version} → {result.latest_version}"
        ),
        severity="info",
        source_id="update_checker",
        priority=Priority.NORMAL,
    )

    try:
        await app_state_repo.set(DEDUP_KEY, result.latest_version)
    except Exception as exc:
        _log.warning("Failed to update dedup state: {err}", err=exc)
