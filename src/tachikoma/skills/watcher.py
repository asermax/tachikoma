"""Filesystem watcher for hot-reloading skills at runtime.

Monitors the skills directory for changes and signals the registry
to refresh before the next session's classification pass.
"""

import asyncio
from pathlib import Path

from bubus import EventBus
from loguru import logger
from watchfiles import awatch

from tachikoma.skills.events import SkillsChanged
from tachikoma.skills.registry import SkillRegistry

_log = logger.bind(component="skills_watcher")


async def watch_skills(
    skills_path: Path,
    registry: SkillRegistry,
    bus: EventBus,
) -> None:
    """Watch the skills directory for changes and trigger registry refresh.

    Uses watchfiles with 5-second debounce to coalesce burst changes
    during skill authoring. When changes are detected:
    1. Marks the registry dirty (triggers re-scan on next provide())
    2. Dispatches SkillsChanged event for other consumers

    The watcher runs as a background asyncio task and handles graceful
    shutdown via CancelledError (propagates naturally through awatch).

    Args:
        skills_path: Path to the workspace/skills directory.
        registry: Shared SkillRegistry to mark dirty on changes.
        bus: EventBus for dispatching SkillsChanged events.
    """
    # Defensive check: skills_hook guarantees directory exists, but guard anyway
    if not skills_path.exists():
        _log.warning(
            "Skills directory does not exist, watcher not starting: path={path}",
            path=str(skills_path),
        )
        return

    _log.info("Skills watcher started: path={path}", path=str(skills_path))

    try:
        async for changes in awatch(
            skills_path,
            debounce=5000,
            rust_timeout=500,
        ):
            _log.debug(
                "Skills change detected: count={count}",
                count=len(changes),
            )

            registry.mark_dirty()
            await bus.dispatch(SkillsChanged())

            _log.info(
                "Skills registry marked for refresh: skills_path={path}",
                path=str(skills_path),
            )

    except asyncio.CancelledError:
        # Expected during shutdown — let it propagate
        _log.debug("Skills watcher cancelled")
        raise

    except Exception as exc:
        # Best-effort: registry keeps last known state, skills work but won't hot-reload
        _log.error(
            "Skills watcher encountered an error, stopping: err={err}",
            err=str(exc),
        )
