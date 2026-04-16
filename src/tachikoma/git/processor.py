"""Git post-processor for committing workspace changes.

Spawns a Haiku agent to inspect and commit workspace changes after each session.
"""

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import HookMatcher
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import PUSH_RESULT, PUSH_SUCCESS, has_uncommitted_changes, smart_push
from tachikoma.post_processing import PostProcessor, build_permissions_settings, make_bash_gate_hook
from tachikoma.sdk_query import stderr_aware_query
from tachikoma.sessions.model import Session

_log = logger.bind(component="git")

GIT_COMMIT_PROMPT = """You are a git commit agent. Your task is to inspect the workspace
and create cohesive, well-organized commits for ALL changes.

## Instructions

1. Run `git status` to see all uncommitted changes (both modified and untracked files).

2. Run `git diff` to understand what changed in modified files.

3. Group the changes into cohesive sets by subdirectory/purpose:
   - Changes in `$WORKSPACE/memories/episodic/` → one commit
   - Changes in `$WORKSPACE/memories/facts/` → one commit
   - Changes in `$WORKSPACE/memories/preferences/` → one commit
   - Changes in `$WORKSPACE/context/` (core context files) → one commit
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

- For git, use only: `git status`, `git diff`, `git add`, `git commit`
- Do NOT use: `git push`, `git branch`, `git checkout`, `git reset`, `git rebase`,
  `git merge`, `git stash`, or any other destructive/history-rewriting commands
- Read-only inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`,
  `head`, `tail`, `wc`, `stat`) are allowed for understanding workspace state
- Navigation commands (`cd`, `pwd`) are allowed for moving between directories
- Never ask for confirmation — just make the commits
- Commit EVERYTHING that shows up in `git status`, including ephemeral runtime files
  (session data, logs, caches). Anything not in `.gitignore` should be committed.
  Do NOT skip files because they look temporary — if git tracks them, commit them.
- If there are no changes, do nothing

Remember: These commits provide version history for the workspace. Good commit
messages help understand what changed and when.

## Permissions

You can read and modify files anywhere in the workspace. For Bash, `git` \
commands and read-only inspection commands (`ls`, `find`, `file`, `echo`, \
`date`, `cat`, `head`, `tail`, `wc`, `stat`) are allowed — other commands \
will be denied. Navigation commands (`cd`, `pwd`) are also allowed."""


GIT_TOOLS = ["Read", "Glob", "Grep", "Bash", "Edit", "Write"]
GIT_ALLOW = ["Read", "Glob", "Grep", "Edit", "Write", "Bash(git *)"]
GIT_BASH_HOOK = make_bash_gate_hook(
    [
        "git ",
        "ls ",
        "find ",
        "file ",
        "echo ",
        "date ",
        "cat ",
        "head ",
        "tail ",
        "wc ",
        "stat ",
        "cd",
        "pwd",
    ]
)


async def query_and_consume(
    prompt: str,
    agent_defaults: AgentDefaults,
    tools: list[str] | None = None,
    allow: list[str] | None = None,
    pre_tool_use_hooks: list[HookMatcher] | None = None,
    model: str | None = None,
) -> None:
    """Spawn a fresh agent and consume its response.

    Creates a fresh query() call with no session forking. Used for
    tasks that don't need conversation context.

    When ``tools`` and ``allow`` are provided, the agent uses
    ``dontAsk`` permission mode with explicit allow rules instead of
    ``bypassPermissions``.

    Args:
        prompt: The prompt to send to the agent.
        agent_defaults: Common SDK options (cwd, cli_path, env).
        tools: Optional tool restriction list for the agent.
        allow: Optional allow-only permission rules for scoping.
        pre_tool_use_hooks: Optional PreToolUse hook matchers.
        model: Optional model alias for the spawned agent. When None
            (the default), the SDK default model is used.

    Raises:
        Propagates: SDK errors from the query() call.
    """
    options = ClaudeAgentOptions(
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
    )

    if model is not None:
        options.model = model

    if tools is not None and allow is not None:
        options.tools = tools
        options.settings = build_permissions_settings(allow)
        options.extra_args = {"permission-mode": "dontAsk"}
    else:
        options.permission_mode = "bypassPermissions"

    if pre_tool_use_hooks is not None:
        options.hooks = {"PreToolUse": pre_tool_use_hooks}

    _log.debug("Spawning query agent")

    # Fully consume the async iterator to ensure the agent completes
    async for _ in stderr_aware_query(prompt=prompt, options=options):
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
        is_dirty = await has_uncommitted_changes(self._cwd)

        if not is_dirty:
            _log.debug("Workspace is clean, no commits needed")
            return

        _log.debug("Workspace has uncommitted changes, spawning commit agent")

        # Spawn agent to handle commits
        prompt = GIT_COMMIT_PROMPT.replace("$WORKSPACE", str(self._cwd))
        await query_and_consume(
            prompt,
            self._agent_defaults,
            tools=GIT_TOOLS,
            allow=GIT_ALLOW,
            pre_tool_use_hooks=[GIT_BASH_HOOK],
            model=self._agent_defaults.processor_model,
        )

        # Push to remote with divergence detection
        result = await smart_push(self._cwd, "origin", "HEAD", self._agent_defaults)

        if result in PUSH_SUCCESS:
            _log.info("Pushed workspace changes: result={result}", result=result)
        elif result == PUSH_RESULT["NOTHING_TO_PUSH"]:
            _log.debug("Nothing to push")
        else:
            _log.warning(
                "Push failed, changes remain committed locally: result={result}",
                result=result,
            )

        # Verify all changes were committed
        still_dirty = await has_uncommitted_changes(self._cwd)
        if still_dirty:
            _log.warning("Uncommitted changes remain after git processor")

        _log.info("Processor completed: processor=GitProcessor")
