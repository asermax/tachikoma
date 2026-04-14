"""Bootstrap hook for git repository initialization and workspace sync.

Initializes the workspace as a git repo on first run (idempotent),
then syncs with the origin remote if configured.
"""

from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import AgentDefaults, merge_env
from tachikoma.bootstrap import BootstrapContext
from tachikoma.git.sync import SYNC_RESULT, _run_git, smart_pull

_log = logger.bind(component="git")

# Fixed committer identity for all git commits
_COMMITTER_NAME = "Tachikoma"
_COMMITTER_EMAIL = "tachikoma@local"


async def git_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize workspace as a git repo and sync with remote.

    Creates a git repository with repo-local identity configuration.
    Idempotent — safe to call on every launch.
    After initialization, syncs the workspace with the origin remote.

    Args:
        ctx: Bootstrap context with settings manager.

    Raises:
        RuntimeError: If any git command fails during initialization.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    git_dir = workspace_path / ".git"

    # Init block: only runs if .git doesn't exist
    if not git_dir.exists():
        _log.info("Initializing git repo: path={path}", path=str(workspace_path))

        await _run_git("init", cwd=workspace_path)
        await _run_git("config", "user.name", _COMMITTER_NAME, cwd=workspace_path)
        await _run_git("config", "user.email", _COMMITTER_EMAIL, cwd=workspace_path)
        await _run_git("commit", "--allow-empty", "-m", "Initial commit", cwd=workspace_path)

        _log.info("Git repo initialized successfully")

    # Sync block: always runs after init check
    await _sync_workspace(workspace_path, ctx.settings_manager.settings)


async def _sync_workspace(workspace_path: Path, settings) -> None:
    """Sync workspace with origin remote using smart_pull.

    Non-blocking — all errors are caught and logged so startup continues.

    Args:
        workspace_path: Path to the workspace git repository.
        settings: Application settings for building agent defaults.
    """
    try:
        # Check if origin remote is configured
        try:
            await _run_git("remote", "get-url", "origin", cwd=workspace_path)
        except RuntimeError:
            _log.debug("No origin remote configured, skipping sync")
            return

        # Build agent defaults following the same pattern as __main__.py
        merged_env = merge_env(settings.agent.env, auto_injected={"TZ": settings.tasks.timezone})
        agent_defaults = AgentDefaults(
            cwd=workspace_path,
            cli_path=settings.agent.cli_path,
            env=merged_env,
        )

        result = await smart_pull(workspace_path, "origin", "HEAD", agent_defaults)

        # Log result
        if result == SYNC_RESULT["DIRTY_SKIPPED"]:
            _log.warning("Workspace has uncommitted changes, skipping sync")
        elif result == SYNC_RESULT["UP_TO_DATE"]:
            _log.debug("Workspace already up to date")
        elif result in (
            SYNC_RESULT["FAST_FORWARDED"],
            SYNC_RESULT["REBASE_SUCCEEDED"],
            SYNC_RESULT["AGENT_RESOLVED"],
        ):
            _log.info("Workspace synced: result={result}", result=result)
        elif result == SYNC_RESULT["SYNC_FAILED"]:
            _log.warning("Workspace sync failed, continuing with local state")

    except Exception as e:
        _log.warning("Workspace sync failed: err={err}", err=str(e))
