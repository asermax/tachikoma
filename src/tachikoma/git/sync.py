"""Shared sync utilities for keeping local repos in sync with remotes.

Provides divergence detection, smart push (fetch→detect→rebase→push),
and smart pull (fetch→detect→rebase) with agent-driven conflict resolution
when naive rebase fails.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.sdk_query import stderr_aware_query

_log = logger.bind(component="git.sync")

# --- Result Enums ---

DIVERGENCE_STATUS = {
    "UP_TO_DATE": "UP_TO_DATE",
    "AHEAD": "AHEAD",
    "BEHIND": "BEHIND",
    "DIVERGED": "DIVERGED",
}
DivergenceStatus = str
"""Divergence status between local and remote branches."""


PUSH_RESULT = {
    "PUSHED": "PUSHED",
    "NOTHING_TO_PUSH": "NOTHING_TO_PUSH",
    "REBASE_SUCCEEDED": "REBASE_SUCCEEDED",
    "AGENT_RESOLVED": "AGENT_RESOLVED",
    "PUSH_FAILED": "PUSH_FAILED",
    "REBASE_FAILED": "REBASE_FAILED",
}
PushResult = str
"""Result of a smart_push operation."""


SYNC_RESULT = {
    "UP_TO_DATE": "UP_TO_DATE",
    "FAST_FORWARDED": "FAST_FORWARDED",
    "REBASE_SUCCEEDED": "REBASE_SUCCEEDED",
    "AGENT_RESOLVED": "AGENT_RESOLVED",
    "SYNC_FAILED": "SYNC_FAILED",
    "DIRTY_SKIPPED": "DIRTY_SKIPPED",
}
SyncResult = str
"""Result of a smart_pull operation."""


# Push results that indicate successful push (used by processors for result logging)
PUSH_SUCCESS = frozenset(
    {
        PUSH_RESULT["PUSHED"],
        PUSH_RESULT["REBASE_SUCCEEDED"],
        PUSH_RESULT["AGENT_RESOLVED"],
    }
)


# --- Git Subprocess Helpers ---


async def run_git(*args: str, cwd: Path) -> None:
    """Run a git command and raise on failure.

    Args:
        *args: Git command arguments.
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


async def run_git_capture(*args: str, cwd: Path) -> tuple[int, str]:
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


async def has_uncommitted_changes(cwd: Path) -> bool:
    """Check if the working tree has uncommitted changes.

    Args:
        cwd: The repository directory.

    Returns:
        True if there are uncommitted changes, False if clean.
    """
    _, output = await run_git_capture("status", "--porcelain", cwd=cwd)
    return bool(output)


def _rebase_in_progress(cwd: Path) -> bool:
    """Check if a rebase is currently in progress by inspecting git state dirs."""
    git_dir = cwd / ".git"
    return (git_dir / "rebase-merge").exists() or (git_dir / "rebase-apply").exists()


async def _abort_stale_rebase(cwd: Path) -> bool:
    """Detect and abort any in-progress rebase.

    Checks for .git/rebase-merge/ and .git/rebase-apply/ directories.
    If found, runs git rebase --abort and returns True.

    Args:
        cwd: The repository directory.

    Returns:
        True if a stale rebase was aborted, False if clean.
    """
    if not _rebase_in_progress(cwd):
        return False

    _log.warning("Stale rebase detected, aborting: path={path}", path=str(cwd))

    try:
        await run_git("rebase", "--abort", cwd=cwd)
    except Exception as e:
        _log.warning(
            "Failed to abort stale rebase: path={path} err={err}",
            path=str(cwd),
            err=str(e),
        )

    return True


# --- Core Sync Functions ---


async def detect_divergence(
    cwd: Path,
    remote: str = "origin",
    branch: str = "HEAD",
) -> DivergenceStatus:
    """Detect divergence between local and remote branches.

    Precondition: git fetch <remote> has already been called.

    Uses git merge-base --is-ancestor in both directions to classify
    the relationship between local HEAD and remote/branch.

    Args:
        cwd: The repository directory.
        remote: The remote name (default "origin").
        branch: The branch name (default "HEAD").

    Returns:
        DivergenceStatus indicating the relationship.

    Raises:
        RuntimeError: If git commands fail unexpectedly.
    """
    remote_ref = f"{remote}/{branch}"

    # Is remote/branch an ancestor of HEAD? (local is at least AHEAD)
    rc_remote_ancestor, _ = await run_git_capture(
        "merge-base",
        "--is-ancestor",
        remote_ref,
        "HEAD",
        cwd=cwd,
    )
    remote_is_ancestor = rc_remote_ancestor == 0

    # Is HEAD an ancestor of remote/branch? (local is at least BEHIND)
    rc_head_ancestor, _ = await run_git_capture(
        "merge-base",
        "--is-ancestor",
        "HEAD",
        remote_ref,
        cwd=cwd,
    )
    head_is_ancestor = rc_head_ancestor == 0

    if remote_is_ancestor and head_is_ancestor:
        return DIVERGENCE_STATUS["UP_TO_DATE"]
    elif remote_is_ancestor:
        return DIVERGENCE_STATUS["AHEAD"]
    elif head_is_ancestor:
        return DIVERGENCE_STATUS["BEHIND"]
    else:
        return DIVERGENCE_STATUS["DIVERGED"]


async def _try_naive_rebase(cwd: Path, remote_branch: str) -> bool:
    """Attempt a naive git rebase. Returns True if clean, False if conflicts.

    On failure, immediately aborts the rebase to restore clean state.

    Args:
        cwd: The repository directory.
        remote_branch: The remote branch ref (e.g., "origin/main").

    Returns:
        True if rebase succeeded cleanly, False if conflicts detected.
    """
    rc, _ = await run_git_capture("rebase", remote_branch, cwd=cwd)

    if rc == 0:
        return True

    # Rebase failed — only abort if a rebase is actually in progress
    if _rebase_in_progress(cwd):
        try:
            await run_git("rebase", "--abort", cwd=cwd)
        except Exception as e:
            _log.warning(
                "Failed to abort rebase after conflict: path={path} err={err}",
                path=str(cwd),
                err=str(e),
            )
    else:
        _log.warning(
            "Rebase failed without starting (no conflicts): path={path}",
            path=str(cwd),
        )

    return False


CONFLICT_RESOLUTION_PROMPT = """You are resolving git conflicts during a rebase operation.

Run: GIT_EDITOR=true git rebase {remote_branch}

If conflicts are detected:
1. Read each conflicted file to understand the content
2. Resolve conflicts by understanding what the files contain and
   intelligently merging the content — do NOT simply pick one side
3. Run: git add <resolved-files>
4. Run: GIT_EDITOR=true git rebase --continue
5. Repeat until the rebase completes fully

If you cannot resolve a conflict (the changes are truly incompatible):
1. Run: git rebase --abort
2. Report that the conflicts are unresolvable

Do NOT run git push. Do NOT modify files unrelated to the conflicts."""


async def _agent_rebase(cwd: Path, remote_branch: str, agent_defaults: AgentDefaults) -> bool:
    """Spawn a Haiku agent to resolve rebase conflicts.

    The agent handles the full rebase cycle: running git rebase,
    resolving conflicts file-by-file, and continuing until complete.

    Success is determined by checking if .git/rebase-merge/ still exists
    after the agent completes — filesystem state is deterministic.

    Args:
        cwd: The repository directory.
        remote_branch: The remote branch ref (e.g., "origin/main").
        agent_defaults: Common SDK options for agent construction.

    Returns:
        True if agent completed the rebase, False if it failed.
    """
    # See ADR for agent-driven full rebase approach
    options = ClaudeAgentOptions(
        model=agent_defaults.processor_model,
        cwd=cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        disallowed_tools=list(agent_defaults.disallowed_tools),
        permission_mode="bypassPermissions",
    )

    prompt = CONFLICT_RESOLUTION_PROMPT.format(remote_branch=remote_branch)

    _log.info(
        "Spawning conflict resolution agent: path={path} remote_branch={branch}",
        path=str(cwd),
        branch=remote_branch,
    )

    try:
        # Fully consume the generator per DES-005
        async for _ in stderr_aware_query(prompt=prompt, options=options):
            pass
    except Exception as e:
        _log.warning(
            "Conflict resolution agent failed: path={path} err={err}",
            path=str(cwd),
            err=str(e),
        )

    # Check if rebase completed by inspecting filesystem state
    if not _rebase_in_progress(cwd):
        _log.info("Conflict resolution agent completed rebase: path={path}", path=str(cwd))
        return True

    # Agent failed — rebase dir still exists, abort to clean up
    _log.warning(
        "Conflict resolution agent left rebase in progress, aborting: path={path}",
        path=str(cwd),
    )

    try:
        await run_git("rebase", "--abort", cwd=cwd)
    except Exception as e:
        _log.warning(
            "Failed to abort after agent failure: path={path} err={err}",
            path=str(cwd),
            err=str(e),
        )

    return False


async def smart_push(
    cwd: Path,
    remote: str = "origin",
    branch: str = "HEAD",
    agent_defaults: AgentDefaults | None = None,
) -> PushResult:
    """Push local commits to remote with divergence detection and conflict resolution.

    Flow:
    1. Abort any stale rebase
    2. Fetch from remote
    3. Detect divergence
    4. If ahead → push directly
    5. If diverged → try naive rebase → try agent rebase → push
    6. All failures are caught and returned as result enums

    Args:
        cwd: The repository directory.
        remote: The remote name (default "origin").
        branch: The branch name (default "HEAD").
        agent_defaults: Required for conflict resolution. If None,
            agent resolution is skipped (rebase failures return REBASE_FAILED).

    Returns:
        PushResult indicating what happened.
    """
    try:
        # Clean up any stale rebase state
        await _abort_stale_rebase(cwd)

        # Always fetch first (R8)
        await run_git("fetch", remote, cwd=cwd)

        divergence = await detect_divergence(cwd, remote, branch)

        if divergence in (DIVERGENCE_STATUS["UP_TO_DATE"], DIVERGENCE_STATUS["BEHIND"]):
            return PUSH_RESULT["NOTHING_TO_PUSH"]

        if divergence == DIVERGENCE_STATUS["AHEAD"]:
            await run_git("push", remote, "HEAD", cwd=cwd)
            return PUSH_RESULT["PUSHED"]

        # DIVERGED — attempt rebase
        remote_branch = f"{remote}/{branch}"

        if await _try_naive_rebase(cwd, remote_branch):
            # Naive rebase succeeded — push
            try:
                await run_git("push", remote, "HEAD", cwd=cwd)
                return PUSH_RESULT["REBASE_SUCCEEDED"]
            except Exception as e:
                _log.warning(
                    "Push failed after successful rebase: path={path} err={err}",
                    path=str(cwd),
                    err=str(e),
                )
                return PUSH_RESULT["PUSH_FAILED"]

        # Naive rebase failed — only try agent if conflicts actually exist
        if agent_defaults is None:
            _log.warning("Rebase failed but no agent_defaults provided for conflict resolution")
            return PUSH_RESULT["REBASE_FAILED"]

        if not _rebase_in_progress(cwd):
            _log.warning(
                "Rebase failed but no conflicts detected, skipping agent resolution: path={path}",
                path=str(cwd),
            )
            return PUSH_RESULT["REBASE_FAILED"]

        if await _agent_rebase(cwd, remote_branch, agent_defaults):
            # Agent resolved — push
            try:
                await run_git("push", remote, "HEAD", cwd=cwd)
                return PUSH_RESULT["AGENT_RESOLVED"]
            except Exception as e:
                _log.warning(
                    "Push failed after agent resolution: path={path} err={err}",
                    path=str(cwd),
                    err=str(e),
                )
                return PUSH_RESULT["PUSH_FAILED"]

        # Agent failed — local commits preserved
        return PUSH_RESULT["REBASE_FAILED"]

    except Exception as e:
        _log.warning("Smart push failed: path={path} err={err}", path=str(cwd), err=str(e))
        return PUSH_RESULT["REBASE_FAILED"]


async def _get_changed_files(cwd: Path, old_head: str) -> list[str]:
    """Get list of files changed between old_head and current HEAD.

    Args:
        cwd: The repository directory.
        old_head: The commit hash before the pull operation.

    Returns:
        List of file paths changed between old_head and HEAD.
    """
    _, output = await run_git_capture("diff", "--name-only", old_head, "HEAD", cwd=cwd)
    return output.splitlines() if output else []


async def smart_pull(
    cwd: Path,
    remote: str = "origin",
    branch: str = "HEAD",
    agent_defaults: AgentDefaults | None = None,
) -> tuple[SyncResult, list[str]]:
    """Pull remote changes with divergence detection and conflict resolution.

    Flow:
    1. Check for uncommitted changes → skip if dirty
    2. Abort any stale rebase
    3. Fetch from remote
    4. Detect divergence
    5. If behind → rebase (fast-forward)
    6. If diverged → try naive rebase → try agent rebase

    Args:
        cwd: The repository directory.
        remote: The remote name (default "origin").
        branch: The branch name (default "HEAD").
        agent_defaults: Required for conflict resolution. If None,
            agent resolution is skipped (rebase failures return SYNC_FAILED).

    Returns:
        Tuple of (SyncResult, list of changed file paths).
        Changed files list is empty for non-success results.
    """
    try:
        # Skip sync if working tree is dirty
        if await has_uncommitted_changes(cwd):
            _log.warning(
                "Working tree has uncommitted changes, skipping sync: path={path}",
                path=str(cwd),
            )
            return SYNC_RESULT["DIRTY_SKIPPED"], []

        # Clean up any stale rebase state
        await _abort_stale_rebase(cwd)

        # Capture HEAD before pull for change detection
        _, old_head = await run_git_capture("rev-parse", "HEAD", cwd=cwd)

        # Always fetch first (R8)
        await run_git("fetch", remote, cwd=cwd)

        divergence = await detect_divergence(cwd, remote, branch)

        if divergence == DIVERGENCE_STATUS["UP_TO_DATE"]:
            return SYNC_RESULT["UP_TO_DATE"], []

        remote_branch = f"{remote}/{branch}"

        if divergence == DIVERGENCE_STATUS["BEHIND"]:
            # Fast-forward via rebase
            await run_git("rebase", remote_branch, cwd=cwd)
            changed = await _get_changed_files(cwd, old_head)
            return SYNC_RESULT["FAST_FORWARDED"], changed

        # DIVERGED — attempt rebase
        if await _try_naive_rebase(cwd, remote_branch):
            changed = await _get_changed_files(cwd, old_head)
            return SYNC_RESULT["REBASE_SUCCEEDED"], changed

        # Naive rebase failed — only try agent if conflicts actually exist
        if agent_defaults is None:
            _log.warning("Rebase failed but no agent_defaults provided for conflict resolution")
            return SYNC_RESULT["SYNC_FAILED"], []

        if not _rebase_in_progress(cwd):
            _log.warning(
                "Rebase failed but no conflicts detected, skipping agent resolution: path={path}",
                path=str(cwd),
            )
            return SYNC_RESULT["SYNC_FAILED"], []

        if await _agent_rebase(cwd, remote_branch, agent_defaults):
            changed = await _get_changed_files(cwd, old_head)
            return SYNC_RESULT["AGENT_RESOLVED"], changed

        return SYNC_RESULT["SYNC_FAILED"], []

    except Exception as e:
        _log.warning("Smart pull failed: path={path} err={err}", path=str(cwd), err=str(e))
        return SYNC_RESULT["SYNC_FAILED"], []
