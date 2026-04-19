"""Bootstrap hook for git repository initialization and workspace sync.

Initializes the workspace as a git repo on first run (idempotent),
then syncs with the origin remote if configured.
"""

import re
import shutil
from pathlib import Path

from loguru import logger

from tachikoma.agent_defaults import agent_defaults_from_settings
from tachikoma.bootstrap import BootstrapContext
from tachikoma.git.sync import SYNC_RESULT, run_git, smart_pull

_log = logger.bind(component="git")

# Fixed committer identity for all git commits
_COMMITTER_NAME = "Tachikoma"
_COMMITTER_EMAIL = "tachikoma@local"

# LFS tracking entry added to .gitattributes on fresh init. Scoped to the
# tachikoma data dir so only the runtime SQLite DB goes through LFS — any
# user-authored .db in a project subdir stays untouched.
_LFS_ATTRIBUTE_LINE = ".tachikoma/*.db filter=lfs diff=lfs merge=lfs -text\n"

# Detects any .gitattributes line that routes *.db through the LFS filter.
# Used for the idempotent-path check; the exact path prefix doesn't matter
# as long as the runtime DB is covered.
_DB_LFS_PATTERN = re.compile(r"\.db\b.*filter=lfs")


async def git_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize workspace as a git repo and sync with remote.

    Creates a git repository with repo-local identity configuration.
    Idempotent — safe to call on every launch.
    After initialization, syncs the workspace with the origin remote.

    Args:
        ctx: Bootstrap context with settings manager.

    Raises:
        RuntimeError: If any git command fails during initialization, or if
            `git-lfs` is not installed on the host (required — see ADR-012).
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    git_dir = workspace_path / ".git"

    _ensure_git_lfs_available()

    # Init block: only runs if .git doesn't exist
    if not git_dir.exists():
        _log.info("Initializing git repo: path={path}", path=str(workspace_path))

        await run_git("init", cwd=workspace_path)
        await run_git("config", "user.name", _COMMITTER_NAME, cwd=workspace_path)
        await run_git("config", "user.email", _COMMITTER_EMAIL, cwd=workspace_path)
        await run_git("commit", "--allow-empty", "-m", "Initial commit", cwd=workspace_path)

        await _configure_lfs(workspace_path)

        _log.info("Git repo initialized successfully")
    else:
        _warn_if_lfs_not_tracked(workspace_path)

    # Sync block: always runs after init check
    await _sync_workspace(workspace_path, ctx.settings_manager.settings)


def _ensure_git_lfs_available() -> None:
    """Fail fast if `git-lfs` is not installed on the host.

    LFS is required because `.gitattributes` routes the workspace SQLite DB
    through the LFS filter; without `git-lfs` registered, `git add` would
    silently store the binary instead of an LFS pointer.
    """
    if shutil.which("git-lfs") is None:
        raise RuntimeError(
            "git-lfs is required but not installed. "
            "Install it via your package manager, e.g. "
            "`pacman -S git-lfs`, `apt install git-lfs`, or `brew install git-lfs`. "
            "See ADR-012 for rationale."
        )


async def _configure_lfs(workspace_path: Path) -> None:
    """Enable Git LFS on a freshly initialized workspace.

    Runs `git lfs install --local` (registers filter hooks in .git/config),
    writes the LFS tracking line into .gitattributes (appending if the file
    already exists), and commits .gitattributes as a second commit after the
    initial empty one.
    """
    await run_git("lfs", "install", "--local", cwd=workspace_path)

    attrs_path = workspace_path / ".gitattributes"
    existing = attrs_path.read_text() if attrs_path.exists() else ""

    if _LFS_ATTRIBUTE_LINE not in existing:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        attrs_path.write_text(existing + separator + _LFS_ATTRIBUTE_LINE)

    await run_git("add", ".gitattributes", cwd=workspace_path)
    await run_git("commit", "-m", "Configure LFS for database", cwd=workspace_path)


def _warn_if_lfs_not_tracked(workspace_path: Path) -> None:
    """Log a warning if an existing workspace has no LFS tracking for *.db.

    This only runs on the existing-repo path. Fresh repos get LFS configured
    automatically by `_configure_lfs`. Existing pre-LFS workspaces need a
    one-time `git lfs migrate import` to relocate historical DB blobs.
    """
    attrs_path = workspace_path / ".gitattributes"

    if attrs_path.exists() and any(
        _DB_LFS_PATTERN.search(line) for line in attrs_path.read_text().splitlines()
    ):
        return

    _log.warning(
        "Workspace lacks LFS tracking for *.db; "
        "run `git lfs migrate import --include='.tachikoma/*.db' --everything` "
        "to migrate historical DB blobs out of the git pack"
    )


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
