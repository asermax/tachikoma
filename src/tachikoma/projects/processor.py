"""Post-processor for committing and pushing project changes.

Spawns Haiku agents to commit changes in dirty submodules after each session.
"""

import asyncio
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from loguru import logger

from tachikoma.post_processing import PostProcessor
from tachikoma.projects.git import is_dirty, list_submodules, push
from tachikoma.sessions.model import Session

_log = logger.bind(component="projects")

SUBMODULE_COMMIT_PROMPT = """You are a git commit agent.
Your task is to inspect a project repository and create cohesive, well-organized commits
for ALL changes.

## Instructions

1. First, run `git log --oneline -10` to learn this project's commit style and conventions.

2. Check for any commit instructions in the repo:
   - Look for CONTRIBUTING.md, CLAUDE.md, or similar files
   - Follow any project-specific commit guidelines

3. Run `git status` to see all uncommitted changes.

4. Run `git diff` to understand what changed.

5. Group the changes into cohesive sets by purpose/directory:
   - Group related changes together
   - Each group should represent one logical change

6. For each group, create a commit:
   - Use `git add <files>` to stage the files in that group
   - Use `git commit -m "<descriptive message>"` with a message that:
     - Describes what changed and why
     - Follows the project's own commit style (from step 1)
     - Is concise but informative

## Important Constraints

- ONLY use these git commands: `git status`, `git diff`, `git log`, `git add`, `git commit`
- Do NOT use: `git push`, `git branch`, `git checkout`, `git reset`, `git rebase`,
  `git merge`, `git stash`, or any other commands
- Never ask for confirmation — just make the commits
- Commit EVERYTHING that shows up in `git status`
- If there are no changes, do nothing

Remember: These commits will be pushed to the project's remote. Good commit
messages help other developers understand the project's history."""


class ProjectsProcessor(PostProcessor):
    """Post-processor for committing and pushing project submodule changes.

    Checks each registered submodule for uncommitted changes, spawns a Haiku
    agent per dirty submodule to create descriptive commits, then pushes to
    each submodule's remote. Runs in the pre_finalize phase before GitProcessor.
    """

    def __init__(self, cwd: Path, cli_path: str | None = None) -> None:
        """Initialize the processor.

        Args:
            cwd: The workspace directory.
            cli_path: Optional path to the Claude CLI binary.
        """
        self._cwd = cwd
        self._cli_path = cli_path

    async def process(self, session: Session) -> None:
        """Process dirty submodules: commit and push changes.

        Args:
            session: The closed session (not used, but required by interface).
        """
        # Discover submodules
        submodule_paths = await list_submodules(self._cwd)
        if not submodule_paths:
            _log.debug("No submodules found, skipping project processing")
            return

        # Check which ones are dirty (parallel)
        dirty_results = await asyncio.gather(
            *[self._is_dirty(path) for path in submodule_paths],
            return_exceptions=True,
        )

        # Filter to dirty submodules
        dirty_paths: list[str] = []
        for path, result in zip(submodule_paths, dirty_results, strict=True):
            if isinstance(result, Exception):
                _log.warning(
                    "Failed to check submodule status: path={path} err={err}",
                    path=path,
                    err=str(result),
                )
            elif result is True:
                dirty_paths.append(path)

        if not dirty_paths:
            _log.debug("No dirty submodules, skipping commit")
            return

        _log.info(
            "Processing dirty submodules: count={count} paths={paths}",
            count=len(dirty_paths),
            paths=dirty_paths,
        )

        # Commit and push each dirty submodule in parallel
        results = await asyncio.gather(
            *[self._commit_and_push(path) for path in dirty_paths],
            return_exceptions=True,
        )

        # Log failures
        for path, result in zip(dirty_paths, results, strict=True):
            if isinstance(result, Exception):
                _log.warning(
                    "Failed to process submodule: path={path} err={err}",
                    path=path,
                    err=str(result),
                )

    async def _is_dirty(self, path: str) -> bool:
        """Check if a submodule has uncommitted changes.

        Args:
            path: The submodule path.

        Returns:
            True if dirty, False if clean.
        """
        submodule_path = self._cwd / path
        return await is_dirty(submodule_path)

    async def _commit_and_push(self, path: str) -> None:
        """Commit changes in a submodule and push to remote.

        Args:
            path: The submodule path.
        """
        submodule_path = self._cwd / path

        # Spawn agent to create commits
        _log.debug("Spawning commit agent: path={path}", path=path)
        await self._query_and_consume(submodule_path)

        # Push to remote
        try:
            await push(submodule_path)
            _log.info("Pushed submodule changes: path={path}", path=path)
        except Exception as e:
            # Log but don't fail - changes are committed locally
            _log.warning(
                "Push failed, changes remain committed locally: path={path} err={err}",
                path=path,
                err=str(e),
            )

    async def _query_and_consume(self, cwd: Path) -> None:
        """Spawn a fresh agent and consume its response.

        Args:
            cwd: The working directory for the agent.
        """
        options = ClaudeAgentOptions(
            model="haiku",
            cwd=cwd,
            cli_path=self._cli_path,
            permission_mode="bypassPermissions",
        )

        # Fully consume the async iterator to ensure the agent completes
        async for _ in query(prompt=SUBMODULE_COMMIT_PROMPT, options=options):
            pass
