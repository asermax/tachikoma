"""Shared async git helpers for submodule operations.

Provides subprocess wrappers for all git operations needed by the projects
package: hooks, processor, tools, and context provider.
"""

import asyncio
import shutil
from pathlib import Path

from loguru import logger

_log = logger.bind(component="projects")


async def _run_git(*args: str, cwd: Path) -> None:
    """Run a git command and raise on failure.

    Args:
        *args: Git command arguments (e.g., "checkout", "main").
        cwd: Working directory for the command.

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

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git {' '.join(args)} failed: {error_msg}")


async def _run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
    """Run a git command and return exit code + stdout.

    Args:
        *args: Git command arguments.
        cwd: Working directory for the command.

    Returns:
        Tuple of (returncode, stdout as decoded string).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()
    return proc.returncode or 0, stdout.decode().strip()


async def list_submodules(workspace: Path) -> list[str]:
    """List all submodule paths via `git submodule status`.

    Args:
        workspace: The workspace root directory.

    Returns:
        List of submodule paths (e.g., ["projects/my-app"]).
    """
    rc, output = await _run_git_capture("submodule", "status", "--recursive", cwd=workspace)

    if rc != 0:
        return []

    # Each line is like " abc1234 projects/my-app (heads/main)"
    # First character is a status indicator: space, +, -, or U
    paths: list[str] = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            paths.append(parts[1])

    return paths


async def init_submodule(workspace: Path, path: str) -> None:
    """Initialize a submodule via `git submodule update --init`.

    Args:
        workspace: The workspace root directory.
        path: The submodule path (e.g., "projects/my-app").

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("submodule", "update", "--init", path, cwd=workspace)


async def resolve_default_branch(submodule_path: Path) -> str:
    """Resolve the default branch from the remote's HEAD reference.

    Tries `git symbolic-ref refs/remotes/origin/HEAD` first (local read).
    Falls back to `git remote show origin` (network call) if needed.
    Final fallback: return "main".

    Args:
        submodule_path: The submodule directory.

    Returns:
        The default branch name (e.g., "main", "master").
    """
    rc, output = await _run_git_capture(
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        cwd=submodule_path,
    )

    if rc == 0 and output.startswith("refs/remotes/origin/"):
        return output[len("refs/remotes/origin/") :]

    # Fallback: network call via remote show
    rc, output = await _run_git_capture("remote", "show", "origin", cwd=submodule_path)

    if rc == 0:
        for line in output.splitlines():
            if "HEAD branch:" in line:
                return line.split(":")[-1].strip()

    _log.warning(
        "Could not resolve default branch, using 'main': path={path}",
        path=str(submodule_path),
    )
    return "main"


async def checkout_branch(submodule_path: Path, branch: str) -> None:
    """Checkout a branch in the submodule.

    Args:
        submodule_path: The submodule directory.
        branch: The branch name to checkout.

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("checkout", branch, cwd=submodule_path)


async def fetch(submodule_path: Path) -> None:
    """Fetch updates from the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("fetch", cwd=submodule_path)


async def pull(submodule_path: Path) -> None:
    """Pull updates from the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("pull", cwd=submodule_path)


async def has_uncommitted_changes_detail(submodule_path: Path) -> str | None:
    """Get detailed status of uncommitted changes.

    Args:
        submodule_path: The submodule directory.

    Returns:
        The git status --porcelain output, or None if clean.
    """
    _, output = await _run_git_capture("status", "--porcelain", cwd=submodule_path)
    return output if output else None


async def is_dirty(submodule_path: Path) -> bool:
    """Check if the submodule has uncommitted changes.

    Args:
        submodule_path: The submodule directory.

    Returns:
        True if there are uncommitted changes, False otherwise.
    """
    return await has_uncommitted_changes_detail(submodule_path) is not None


async def push(submodule_path: Path) -> None:
    """Push commits to the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("push", cwd=submodule_path)


async def add_submodule(workspace: Path, name: str, url: str) -> None:
    """Add a new git submodule.

    Args:
        workspace: The workspace root directory.
        name: The project name (subdirectory under projects/).
        url: The git remote URL.

    Raises:
        RuntimeError: If the command fails.
    """
    await _run_git("submodule", "add", url, f"projects/{name}", cwd=workspace)


async def remove_submodule(workspace: Path, name: str) -> None:
    """Remove a git submodule completely.

    Runs: git submodule deinit -f projects/<name>
          git rm -f projects/<name>
          rm -rf .git/modules/projects/<name>

    Args:
        workspace: The workspace root directory.
        name: The project name (subdirectory under projects/).

    Raises:
        RuntimeError: If any command fails.
    """
    path = f"projects/{name}"

    await _run_git("submodule", "deinit", "-f", path, cwd=workspace)
    await _run_git("rm", "-f", path, cwd=workspace)

    modules_path = workspace / ".git" / "modules" / "projects" / name
    if modules_path.exists():
        shutil.rmtree(modules_path)


async def current_branch(submodule_path: Path) -> str | None:
    """Get the current branch name, or None if detached HEAD.

    Args:
        submodule_path: The submodule directory.

    Returns:
        The branch name, or None if in detached HEAD state.
    """
    rc, output = await _run_git_capture("symbolic-ref", "--short", "HEAD", cwd=submodule_path)

    if rc != 0:
        return None

    return output or None


async def current_commit_short(submodule_path: Path) -> str:
    """Get the short commit hash of HEAD.

    Args:
        submodule_path: The submodule directory.

    Returns:
        The short commit hash (e.g., "abc1234").
    """
    rc, output = await _run_git_capture("rev-parse", "--short", "HEAD", cwd=submodule_path)

    if rc != 0:
        return "unknown"

    return output or "unknown"
