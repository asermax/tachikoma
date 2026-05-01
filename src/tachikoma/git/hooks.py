"""Bootstrap hook for git repository initialization and workspace sync.

Initializes the workspace as a git repo on first run (idempotent),
then syncs with the origin remote if configured.
"""

from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import agent_defaults_from_settings
from tachikoma.bootstrap import BootstrapContext
from tachikoma.git.sync import SYNC_RESULT, run_git, smart_pull

_log = logger.bind(component="git")

# Fixed committer identity for all git commits
_COMMITTER_NAME = "Tachikoma"
_COMMITTER_EMAIL = "tachikoma@local"

# Gitignore entries ensured on every startup
_GITIGNORE_ENTRIES = [
    ".tachikoma/*.db\n",
    ".tachikoma/logs/tachikoma.log\n",
]


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

        await run_git("init", cwd=workspace_path)
        await run_git("config", "user.name", _COMMITTER_NAME, cwd=workspace_path)
        await run_git("config", "user.email", _COMMITTER_EMAIL, cwd=workspace_path)
        await run_git("commit", "--allow-empty", "-m", "Initial commit", cwd=workspace_path)

        await _create_gitignore(workspace_path)

        _log.info("Git repo initialized successfully")

    # Ensure gitignore entries on every startup (idempotent)
    _ensure_gitignore_entries(workspace_path)

    # Sync block: always runs after init check
    await _sync_workspace(workspace_path, ctx.settings_manager.settings)


async def _create_gitignore(workspace_path: Path) -> None:
    """Create .gitignore with required exclusions on fresh init.

    Writes all gitignore entries, appends if the file already exists,
    and commits the .gitignore as a second commit after the initial
    empty one.
    """
    gitignore_path = workspace_path / ".gitignore"
    existing = gitignore_path.read_text() if gitignore_path.exists() else ""

    new_content = _append_missing_entries(existing)
    if new_content != existing:
        gitignore_path.write_text(new_content)

    await run_git("add", ".gitignore", cwd=workspace_path)
    await run_git("commit", "-m", "Add gitignore for workspace exclusions", cwd=workspace_path)


def _ensure_gitignore_entries(workspace_path: Path) -> None:
    """Append any missing gitignore entries without committing.

    Called on every startup to ensure existing workspaces receive
    new entries. The commit agent picks up the change.
    """
    gitignore_path = workspace_path / ".gitignore"
    existing = gitignore_path.read_text() if gitignore_path.exists() else ""

    new_content = _append_missing_entries(existing)
    if new_content != existing:
        gitignore_path.write_text(new_content)


def _append_missing_entries(existing: str) -> str:
    """Append any missing gitignore entries to existing content."""
    content = existing
    for entry in _GITIGNORE_ENTRIES:
        if entry not in content:
            separator = "" if not content or content.endswith("\n") else "\n"
            content += separator + entry
    return content


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
            await run_git("remote", "get-url", "origin", cwd=workspace_path)
        except RuntimeError:
            _log.debug("No origin remote configured, skipping sync")
            return

        # Build agent defaults following the same pattern as __main__.py
        agent_defaults = agent_defaults_from_settings(settings)

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
