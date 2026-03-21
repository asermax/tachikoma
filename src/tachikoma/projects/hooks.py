"""Bootstrap hook for project submodules.

Initializes and syncs all registered project submodules on startup.
"""

import asyncio
from pathlib import Path

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.projects.git import (
    checkout_branch,
    init_submodule,
    list_submodules,
    pull,
    resolve_default_branch,
)

_log = logger.bind(component="projects")


async def projects_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create projects dir and sync all submodules.

    Creates the projects/ directory (idempotent), discovers all registered
    submodules from .gitmodules, and syncs them in parallel with error isolation.

    Each submodule is:
    1. Initialized via git submodule update --init
    2. Resolved to its default branch (from remote HEAD)
    3. Checked out to the default branch
    4. Pulled to get latest changes (includes fetch)

    Failures are logged and the hook continues with other submodules.
    Each submodule gets one retry on failure.

    Args:
        ctx: Bootstrap context with settings manager.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    projects_dir = workspace_path / "projects"

    projects_dir.mkdir(exist_ok=True)

    # Discover submodules
    submodule_paths = await list_submodules(workspace_path)
    if not submodule_paths:
        _log.debug("No submodules found, skipping sync")
        return

    _log.info(
        "Syncing submodules: count={count} paths={paths}",
        count=len(submodule_paths),
        paths=submodule_paths,
    )

    # Sync in parallel with error isolation
    results = await asyncio.gather(
        *[_sync_submodule_with_retry(workspace_path, path) for path in submodule_paths],
        return_exceptions=True,
    )

    # Log failures
    for path, result in zip(submodule_paths, results, strict=True):
        if isinstance(result, Exception):
            _log.warning(
                "Submodule sync failed after retry: path={path} err={err}",
                path=path,
                err=str(result),
            )


async def _sync_submodule_with_retry(workspace_path: Path, path: str) -> None:
    """Sync a submodule with one retry on failure.

    Args:
        workspace_path: The workspace root directory.
        path: The submodule path (e.g., "projects/my-app").

    Raises:
        Exception: If sync fails after retry.
    """
    try:
        await _sync_submodule(workspace_path, path)
    except Exception as e:
        _log.debug(
            "Submodule sync failed, retrying: path={path} err={err}",
            path=path,
            err=str(e),
        )
        # Retry once
        await _sync_submodule(workspace_path, path)


async def _sync_submodule(workspace_path: Path, path: str) -> None:
    """Sync a submodule: init → resolve → checkout → pull.

    Args:
        workspace_path: The workspace root directory.
        path: The submodule path (e.g., "projects/my-app").

    Raises:
        Exception: If any step fails.
    """
    submodule_path = workspace_path / path

    await init_submodule(workspace_path, path)
    default_branch = await resolve_default_branch(submodule_path)
    await checkout_branch(submodule_path, default_branch)
    await pull(submodule_path)

    _log.debug(
        "Submodule synced: path={path} branch={branch}",
        path=path,
        branch=default_branch,
    )
