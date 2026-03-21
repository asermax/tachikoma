"""Shared async git helpers for submodule operations.

Provides subprocess wrappers for all git operations needed by the projects
package: hooks, processor, tools, and context provider.
"""

import asyncio
import shutil
from pathlib import Path

from loguru import logger

_log = logger.bind(component="projects")


async def list_submodules(workspace: Path) -> list[str]:
    """List all submodule paths via `git submodule status`.

    Args:
        workspace: The workspace root directory.

    Returns:
        List of submodule paths (e.g., ["projects/my-app"]).
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "submodule",
        "status",
        "--recursive",
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        # No submodules or error — return empty list
        return []

    # Parse output: each line is like " abc1234 projects/my-app (heads/main)"
    # The first character is a status indicator: space, +, -, or U
    paths: list[str] = []
    for line in stdout.decode().strip().splitlines():
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
    proc = await asyncio.create_subprocess_exec(
        "git",
        "submodule",
        "update",
        "--init",
        path,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git submodule update --init failed: {error_msg}")


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
    # Try local read first
    proc = await asyncio.create_subprocess_exec(
        "git",
        "symbolic-ref",
        "refs/remotes/origin/HEAD",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()

    if proc.returncode == 0:
        # Output is like "refs/remotes/origin/main"
        ref = stdout.decode().strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref[len("refs/remotes/origin/") :]

    # Fallback: network call via remote show
    proc = await asyncio.create_subprocess_exec(
        "git",
        "remote",
        "show",
        "origin",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await proc.communicate()

    if proc.returncode == 0:
        output = stdout.decode()
        # Look for "HEAD branch: main" or similar
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
    proc = await asyncio.create_subprocess_exec(
        "git",
        "checkout",
        branch,
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git checkout failed: {error_msg}")


async def fetch(submodule_path: Path) -> None:
    """Fetch updates from the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "fetch",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git fetch failed: {error_msg}")


async def pull(submodule_path: Path) -> None:
    """Pull updates from the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "pull",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git pull failed: {error_msg}")


async def is_dirty(submodule_path: Path) -> bool:
    """Check if the submodule has uncommitted changes.

    Args:
        submodule_path: The submodule directory.

    Returns:
        True if there are uncommitted changes, False otherwise.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()
    return bool(stdout.strip())


async def push(submodule_path: Path) -> None:
    """Push commits to the remote.

    Args:
        submodule_path: The submodule directory.

    Raises:
        RuntimeError: If the command fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "push",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git push failed: {error_msg}")


async def add_submodule(workspace: Path, name: str, url: str) -> None:
    """Add a new git submodule.

    Args:
        workspace: The workspace root directory.
        name: The project name (subdirectory under projects/).
        url: The git remote URL.

    Raises:
        RuntimeError: If the command fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "submodule",
        "add",
        url,
        f"projects/{name}",
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git submodule add failed: {error_msg}")


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

    # Deinit
    proc = await asyncio.create_subprocess_exec(
        "git",
        "submodule",
        "deinit",
        "-f",
        path,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git submodule deinit failed: {error_msg}")

    # Remove from index
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rm",
        "-f",
        path,
        cwd=workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git rm failed: {error_msg}")

    # Remove .git/modules directory
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
    proc = await asyncio.create_subprocess_exec(
        "git",
        "symbolic-ref",
        "--short",
        "HEAD",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        return None

    return stdout.decode().strip() or None


async def current_commit_short(submodule_path: Path) -> str:
    """Get the short commit hash of HEAD.

    Args:
        submodule_path: The submodule directory.

    Returns:
        The short commit hash (e.g., "abc1234").
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rev-parse",
        "--short",
        "HEAD",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()

    if proc.returncode != 0:
        return "unknown"

    return stdout.decode().strip() or "unknown"


async def has_uncommitted_changes_detail(submodule_path: Path) -> str | None:
    """Get detailed status of uncommitted changes.

    Args:
        submodule_path: The submodule directory.

    Returns:
        The git status --porcelain output, or None if clean.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        cwd=submodule_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()

    output = stdout.decode().strip()
    return output if output else None
