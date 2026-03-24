"""Git post-processor for committing workspace changes.

Spawns a Haiku agent to inspect and commit workspace changes after each session.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import PostProcessor
from tachikoma.sessions.model import Session

_log = logger.bind(component="git")

GIT_COMMIT_PROMPT = """You are a git commit agent. Your task is to inspect the workspace
and create cohesive, well-organized commits for ALL changes.

## Instructions

1. Run `git status` to see all uncommitted changes (both modified and untracked files).

2. Run `git diff` to understand what changed in modified files.

3. Group the changes into cohesive sets by subdirectory/purpose:
   - Changes in `memories/episodic/` → one commit
   - Changes in `memories/facts/` → one commit
   - Changes in `memories/preferences/` → one commit
   - Changes in `context/` (core context files) → one commit
   - Other workspace files → group logically

4. For each group, create a commit:
   - Use `git add <files>` to stage the files in that group
   - Use `git commit -m "<descriptive message>"` with a message that describes
     what changed and why

5. Commit message guidelines:
   - Be descriptive but concise
   - Mention the type of change (e.g., "Update episodic memories", "Add new user preference")
   - Include the date for time-based files (e.g., "Update episodic memories for 2026-03-13")

## Important Constraints

- ONLY use these git commands: `git status`, `git diff`, `git add`, `git commit`
- Do NOT use: `git push`, `git branch`, `git checkout`, `git reset`, `git rebase`,
  `git merge`, `git stash`, or any other commands
- Never ask for confirmation — just make the commits
- Commit EVERYTHING that shows up in `git status`, including ephemeral runtime files
  (session data, logs, caches). Anything not in `.gitignore` should be committed.
  Do NOT skip files because they look temporary — if git tracks them, commit them.
- If there are no changes, do nothing

Remember: These commits provide version history for the workspace. Good commit
messages help understand what changed and when."""


async def query_and_consume(prompt: str, agent_defaults: AgentDefaults) -> None:
    """Spawn a fresh agent and consume its response.

    Creates a fresh query() call with no session forking. Used for
    tasks that don't need conversation context.

    Args:
        prompt: The prompt to send to the agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).

    Raises:
        Propagates: SDK errors from the query() call.
    """
    options = ClaudeAgentOptions(
        model="haiku",
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        permission_mode="bypassPermissions",
    )

    _log.debug("Spawning query agent")

    # Fully consume the async iterator to ensure the agent completes
    async for _ in query(prompt=prompt, options=options):
        pass

    _log.debug("Query agent completed")


class GitProcessor(PostProcessor):
    """Post-processor for committing and pushing workspace changes.

    Spawns a Haiku agent to inspect and commit changes after each session,
    then pushes to the origin remote if one is configured.
    Runs in the finalize phase after all other processors complete.
    """

    def __init__(self, agent_defaults: AgentDefaults) -> None:
        """Initialize the processor.

        Args:
            agent_defaults: Common SDK options (cwd, cli_path, env).
        """
        self._agent_defaults = agent_defaults
        self._cwd = agent_defaults.cwd

    async def process(self, session: Session) -> None:
        """Commit and push workspace changes if any exist.

        Args:
            session: The closed session (not used, but required by interface).
        """
        _log.info("Processor started: processor=GitProcessor")

        # Check if there are any uncommitted changes
        is_dirty = await _check_git_status(self._cwd)

        if not is_dirty:
            _log.debug("Workspace is clean, no commits needed")
            return

        _log.debug("Workspace has uncommitted changes, spawning commit agent")

        # Spawn agent to handle commits
        await query_and_consume(GIT_COMMIT_PROMPT, self._agent_defaults)

        # Push to remote if configured (partial commits are valid and worth pushing)
        has_remote = await _has_remote(self._cwd)

        if has_remote:
            try:
                await _push(self._cwd)
                _log.info("Pushed workspace changes")
            except Exception as e:
                _log.warning(
                    "Push failed, changes remain committed locally: err={err}",
                    err=str(e),
                )
        else:
            _log.debug("No origin remote configured, skipping push")

        # Verify all changes were committed
        still_dirty = await _check_git_status(self._cwd)
        if still_dirty:
            _log.warning("Uncommitted changes remain after git processor")

        _log.info("Processor completed: processor=GitProcessor")


async def _check_git_status(cwd: Path) -> bool:
    """Check if the workspace has uncommitted changes.

    Args:
        cwd: The workspace directory.

    Returns:
        True if there are uncommitted changes, False if clean.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "status",
        "--porcelain",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, _ = await proc.communicate()
    return bool(stdout.strip())


async def _has_remote(cwd: Path) -> bool:
    """Check if the origin remote is configured.

    Uses ``git remote get-url origin`` which is a local-only check
    (no network call).

    Args:
        cwd: The workspace directory.

    Returns:
        True if origin remote exists, False otherwise.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "remote",
        "get-url",
        "origin",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    await proc.communicate()
    return proc.returncode == 0


async def _push(cwd: Path) -> None:
    """Push commits to the origin remote.

    Uses ``git push origin HEAD`` to avoid dependence on upstream tracking
    configuration.

    Args:
        cwd: The workspace directory.

    Raises:
        RuntimeError: If the push command fails.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "push",
        "origin",
        "HEAD",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
        raise RuntimeError(f"git push failed: {error_msg}")
