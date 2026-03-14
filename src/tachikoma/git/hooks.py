"""Bootstrap hook for git repository initialization.

Initializes the workspace as a git repo on first run (idempotent).
"""

import asyncio
from pathlib import Path

from loguru import logger

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="git")

# Fixed committer identity for all git commits
_COMMITTER_NAME = "Tachikoma"
_COMMITTER_EMAIL = "tachikoma@local"


async def git_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize workspace as a git repo.

    Creates a git repository with repo-local identity configuration.
    Idempotent — safe to call on every launch.

    Args:
        ctx: Bootstrap context with settings manager.

    Raises:
        RuntimeError: If any git command fails.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    git_dir = workspace_path / ".git"

    # Idempotent: skip if already initialized
    if git_dir.exists():
        _log.debug("Git repo already initialized: path={path}", path=str(workspace_path))
        return

    _log.info("Initializing git repo: path={path}", path=str(workspace_path))

    # Run git init
    await _run_git_command(workspace_path, ["init"])

    # Configure repo-local identity
    await _run_git_command(workspace_path, ["config", "user.name", _COMMITTER_NAME])
    await _run_git_command(workspace_path, ["config", "user.email", _COMMITTER_EMAIL])

    # Create initial empty commit
    await _run_git_command(
        workspace_path, ["commit", "--allow-empty", "-m", "Initial commit"]
    )

    _log.info("Git repo initialized successfully")


async def _run_git_command(cwd: Path, args: list[str]) -> None:
    """Run a git command and raise on failure.

    Args:
        cwd: Working directory for the command.
        args: Git command arguments (e.g., ["init"], ["config", "user.name", "X"]).

    Raises:
        RuntimeError: If the command returns non-zero exit code.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {error_msg}")
