"""MCP tools for git push/sync operations.

Exposes two MCP tools — ``push`` and ``sync`` — that wrap the existing
``smart_push`` / ``smart_pull`` helpers from :mod:`tachikoma.git.sync`.
Both tools can target the workspace or a registered project submodule.

Follows DES-006 (SDK MCP Tool Server Factory): factory takes config,
handlers are extracted for testability. Also exports
``DESTRUCTIVE_GIT_DENY_PATTERNS`` — the regex set used by
``make_bash_deny_hook`` to block destructive bash git commands on
non-git-processor agent surfaces.
"""

import asyncio
import re
from pathlib import Path
from typing import Literal

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from loguru import logger
from pydantic import BaseModel

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.processor import GIT_ALLOW, GIT_BASH_HOOK, GIT_TOOLS
from tachikoma.git.sync import (
    PUSH_RESULT,
    SYNC_RESULT,
    has_uncommitted_changes,
    run_git,
    run_git_capture,
    smart_pull,
    smart_push,
)
from tachikoma.mcp_utils import decode_json_string_array
from tachikoma.post_processing import query_and_consume

_log = logger.bind(component="git.tools")


# --- Arg models ---

TargetType = Literal["workspace", "project"]


class PushArgs(BaseModel):
    type: TargetType
    target: str | None = None
    # Declared as a JSON-encoded string (e.g. '["a.ogg", "b.json"]') rather
    # than a list. The SDK MCP transport's client-side schema validator
    # rejects array-typed arguments, so the tool accepts a JSON string and
    # the wrapper parses it via decode_json_string_array. See DES-006.
    scrub_paths: str | None = None


class SyncArgs(BaseModel):
    type: TargetType
    target: str | None = None


# --- Target resolution ---


def resolve_target(
    type_: TargetType,
    target: str | None,
    workspace_path: Path,
) -> Path | None:
    """Resolve a (type, target) pair to a git repo path.

    Returns the workspace root for ``type_ == "workspace"`` (ignoring
    ``target``). For ``type_ == "project"``, returns
    ``workspace_path / "projects" / target`` when ``target`` is non-empty
    and the resolved path contains a ``.git`` directory or file.
    Returns ``None`` otherwise.

    Args:
        type_: Target kind.
        target: Project name when ``type_ == "project"``; ignored otherwise.
        workspace_path: The workspace root directory.

    Returns:
        The resolved repo path or ``None`` when the pair can't be resolved.
    """
    if type_ == "workspace":
        return workspace_path

    if not target:
        return None

    project_path = workspace_path / "projects" / target
    git_marker = project_path / ".git"

    if not git_marker.exists():
        return None

    return project_path


def _describe_target(type_: TargetType, target: str | None) -> str:
    if type_ == "workspace":
        return "workspace"

    return f"project '{target}'" if target else "project (unnamed)"


# --- Failure enum sets ---

_PUSH_FAILURES = frozenset(
    {
        PUSH_RESULT["PUSH_FAILED"],
        PUSH_RESULT["REBASE_FAILED"],
    }
)

_SYNC_FAILURES = frozenset(
    {
        SYNC_RESULT["SYNC_FAILED"],
        SYNC_RESULT["DIRTY_SKIPPED"],
    }
)


def _error(msg: str) -> dict:
    return {"is_error": True, "content": [{"type": "text", "text": msg}]}


# --- Auto-commit helper ---


_AUTO_COMMIT_PROMPT = """You are a git commit agent. Your task is to inspect the repository
and create cohesive, well-organized commits for ALL changes.

## Instructions

1. Run `git status` to see all uncommitted changes (both modified and untracked files).

2. Run `git diff` to understand what changed in modified files.

3. Group the changes into cohesive sets by subdirectory/purpose:
   - Each group should represent one logical change
   - Related changes go in the same commit

4. For each group, create a commit:
   - Use `git add <files>` to stage the files in that group
   - Use `git commit -m "<descriptive message>"` with a message that describes
     what changed and why

5. After committing all groups, run `git status` again to verify the working tree
   is clean. If any files remain uncommitted, you MUST stage and commit them too.

## Constraints

- For git, use only: `git status`, `git diff`, `git add`, `git commit`
- Do NOT use: `git push`, `git branch`, `git checkout`, `git reset`, `git rebase`,
  `git merge`, `git stash`, or any destructive/history-rewriting commands
- Read-only inspection commands (`ls`, `find`, `file`, `echo`, `date`, `cat`,
  `head`, `tail`, `wc`, `stat`) are allowed for understanding repository state
- Navigation commands (`cd`, `pwd`) are allowed
- Never ask for confirmation — just make the commits
- If there are no changes, do nothing
- After you finish, `git status` must show a clean working tree

## Permissions

You can read and modify files anywhere in the repository. For Bash, `git` \
commands and read-only inspection commands (`ls`, `find`, `file`, `echo`, \
`date`, `cat`, `grep`, `head`, `tail`, `wc`, `stat`) are allowed — other commands \
will be denied. Navigation commands (`cd`, `pwd`) are also allowed."""


async def _auto_commit_if_dirty(
    resolved: Path,
    agent_defaults: AgentDefaults,
) -> bool:
    """Commit uncommitted changes via a fast agent if any exist.

    Follows the same pattern as ProjectsProcessor._commit_and_push:
    creates scoped AgentDefaults with the target cwd, then calls
    query_and_consume with the commit prompt and gate hooks.

    Args:
        resolved: The git repository path.
        agent_defaults: Common SDK options (cwd, cli_path, env, models).

    Returns:
        True if changes were committed, False if the repo was clean.

    Raises:
        Propagates: SDK errors from the commit agent.
    """
    if not await has_uncommitted_changes(resolved):
        return False

    scoped_defaults = AgentDefaults(
        cwd=resolved,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        searcher_model=agent_defaults.searcher_model,
        processor_model=agent_defaults.processor_model,
        classifier_model=agent_defaults.classifier_model,
    )

    await query_and_consume(
        _AUTO_COMMIT_PROMPT,
        scoped_defaults,
        tools=GIT_TOOLS,
        allow=GIT_ALLOW,
        pre_tool_use_hooks=[GIT_BASH_HOOK],
        model=scoped_defaults.processor_model,
    )

    return True


# --- Scrub helper ---


async def handle_scrub(
    type_: TargetType,
    target: str | None,
    workspace_path: Path,
    scrub_paths: list[str],
) -> dict:
    """Scrub specified paths from git history via ``git filter-repo``.

    Runs ``git filter-repo --invert-paths --path <each> --force``,
    re-adds the origin remote (which filter-repo removes), then
    force-pushes to origin.

    Args:
        type_: Must be ``"project"``.
        target: Project name.
        workspace_path: The workspace root directory.
        scrub_paths: Non-empty list of paths to remove from history.

    Returns:
        MCP tool response dict.
    """
    if type_ != "project":
        return _error(
            "Error: scrub_paths is only supported for project targets. "
            "Use type='project' with target=<project-name>."
        )

    if not scrub_paths:
        return _error("Error: scrub_paths must be a non-empty list.")

    resolved = resolve_target(type_, target, workspace_path)
    description = _describe_target(type_, target)

    if resolved is None:
        return _error(
            f"Error: could not resolve target ({description}). "
            f"For type='project', provide the project name and ensure "
            f"projects/<name> is a registered submodule."
        )

    try:
        if await has_uncommitted_changes(resolved):
            return _error(
                f"Error: cannot scrub {description}: working tree has "
                "uncommitted changes. Commit or stash them first."
            )

        invalid_paths: list[str] = []
        for path in scrub_paths:
            _, output = await run_git_capture(
                "log",
                "-1",
                "--all",
                "--",
                path,
                cwd=resolved,
            )
            if not output.strip():
                invalid_paths.append(path)

        if invalid_paths:
            return _error(
                f"Error: paths not found in git history: "
                f"{', '.join(invalid_paths)}. "
                "Provide paths that exist in the repository's history."
            )

        rc, origin_url = await run_git_capture(
            "remote",
            "get-url",
            "origin",
            cwd=resolved,
        )
        if rc != 0 or not origin_url.strip():
            return _error(
                f"Error: cannot scrub {description}: no origin remote "
                "configured. An origin remote is required to re-push after "
                "history rewrite."
            )
        origin_url = origin_url.strip()

        filter_args = ["--invert-paths"]
        for path in scrub_paths:
            filter_args.extend(["--path", path])
        filter_args.append("--force")

        _log.info(
            "Running git filter-repo: target={desc} paths={paths}",
            desc=description,
            paths=scrub_paths,
        )

        # filter-repo prompts for confirmation; pipe "yes" to stdin
        proc = await asyncio.create_subprocess_exec(
            "git",
            "filter-repo",
            *filter_args,
            cwd=resolved,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if proc.stdin is None:
            raise RuntimeError("stdin pipe not available for filter-repo")
        proc.stdin.write(b"yes\n" * 10)
        await proc.stdin.drain()
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode().strip() or f"exit code {proc.returncode}"
            _log.warning(
                "git filter-repo failed: target={desc} err={err}",
                desc=description,
                err=error_msg,
            )
            return _error(f"Error: git filter-repo failed for {description}: {error_msg}")

        # filter-repo removes the origin remote
        await run_git("remote", "add", "origin", origin_url, cwd=resolved)

        try:
            await run_git("push", "--force", "origin", "HEAD", cwd=resolved)
        except RuntimeError as exc:
            error_msg = str(exc).removeprefix("git push --force origin HEAD failed: ")
            _log.warning(
                "force push failed after scrub: target={desc} err={err}",
                desc=description,
                err=error_msg,
            )
            return _error(
                f"Scrub completed for {description}, but force push "
                f"failed: {error_msg}. The local repo has been rewritten. "
                "You can retry the push manually."
            )

        _log.info(
            "scrub completed: target={desc} paths={paths}",
            desc=description,
            paths=scrub_paths,
        )

        return {
            "content": [
                {
                    "type": "text",
                    "text": f"scrub {description}: paths {', '.join(scrub_paths)} "
                    "removed from history and force-pushed to origin.",
                }
            ],
        }

    except Exception as e:
        _log.warning(
            "Scrub failed: target={desc} err={err}",
            desc=description,
            err=str(e),
        )
        return _error(f"Error: scrub failed for {description}: {e}")


# --- Handlers ---


async def handle_push(
    type_: TargetType,
    target: str | None,
    workspace_path: Path,
    agent_defaults: AgentDefaults,
    scrub_paths: list[str] | None = None,
) -> dict:
    """Push the current branch of the resolved target to its ``origin`` remote.

    Uses :func:`smart_push` (fetch → divergence detection → rebase → push,
    with agent-driven conflict resolution on diverged branches). When
    ``scrub_paths`` is provided, delegates to :func:`handle_scrub` instead.

    Args:
        type_: Target kind (``"workspace"`` or ``"project"``).
        target: Project name when ``type_ == "project"``.
        workspace_path: The workspace root directory.
        agent_defaults: Common SDK options (used for conflict-resolution
            agent spawning inside ``smart_push``).
        scrub_paths: Optional list of paths to scrub from history (project only).

    Returns:
        MCP tool response dict.
    """
    if scrub_paths is not None:
        return await handle_scrub(type_, target, workspace_path, scrub_paths)
    resolved = resolve_target(type_, target, workspace_path)
    description = _describe_target(type_, target)

    if resolved is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: could not resolve target ({description}). "
                    f"For type='project', provide the project name and ensure "
                    f"projects/<name> is a registered submodule.",
                }
            ],
            "is_error": True,
        }

    try:
        did_commit = await _auto_commit_if_dirty(resolved, agent_defaults)
    except Exception as e:
        _log.warning(
            "Auto-commit failed: target={desc} err={err}",
            desc=description,
            err=str(e),
        )
        return _error(f"Error: auto-commit failed for {description}: {e}")

    result = await smart_push(resolved, "origin", "HEAD", agent_defaults)

    is_error = result in _PUSH_FAILURES
    prefix = "auto-committed, " if did_commit else ""

    _log.info(
        "push tool completed: target={desc} auto_commit={did_commit} result={result}",
        desc=description,
        did_commit=did_commit,
        result=result,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": f"push {description}: {prefix}{result}",
            }
        ],
        "is_error": is_error,
    }


async def handle_sync(
    type_: TargetType,
    target: str | None,
    workspace_path: Path,
    agent_defaults: AgentDefaults,
) -> dict:
    """Sync the resolved target: ``smart_pull`` then ``smart_push``.

    If the pull short-circuits (``DIRTY_SKIPPED`` or ``SYNC_FAILED``),
    the push is not attempted and only the pull result is surfaced.
    Otherwise, both results are surfaced together.

    Args:
        type_: Target kind (``"workspace"`` or ``"project"``).
        target: Project name when ``type_ == "project"``.
        workspace_path: The workspace root directory.
        agent_defaults: Common SDK options (used for conflict-resolution
            agent spawning inside ``smart_pull`` / ``smart_push``).

    Returns:
        MCP tool response dict.
    """
    resolved = resolve_target(type_, target, workspace_path)
    description = _describe_target(type_, target)

    if resolved is None:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: could not resolve target ({description}). "
                    f"For type='project', provide the project name and ensure "
                    f"projects/<name> is a registered submodule.",
                }
            ],
            "is_error": True,
        }

    pull_result = await smart_pull(resolved, "origin", "HEAD", agent_defaults)

    # Short-circuit on pull failure / dirty skip
    if pull_result in _SYNC_FAILURES:
        _log.info(
            "sync tool short-circuited on pull: target={desc} pull_result={pull}",
            desc=description,
            pull=pull_result,
        )
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"sync {description}: pull={pull_result} (push skipped)",
                }
            ],
            "is_error": True,
        }

    push_result = await smart_push(resolved, "origin", "HEAD", agent_defaults)
    is_error = push_result in _PUSH_FAILURES

    _log.info(
        "sync tool completed: target={desc} pull={pull} push={push}",
        desc=description,
        pull=pull_result,
        push=push_result,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": f"sync {description}: pull={pull_result} push={push_result}",
            }
        ],
        "is_error": is_error,
    }


# --- MCP server factory (DES-006) ---


def create_git_tools_server(
    workspace_path: Path,
    agent_defaults: AgentDefaults,
) -> McpSdkServerConfig:
    """Factory: create SDK MCP server with git push/sync tools.

    Args:
        workspace_path: The workspace root directory.
        agent_defaults: Common SDK options threaded through to
            conflict-resolution agents spawned by ``smart_push`` /
            ``smart_pull``.

    Returns:
        McpSdkServerConfig for use with ``ClaudeAgentOptions.mcp_servers``.
    """

    @tool(
        "push",
        (
            "Push the current branch of the workspace or a registered project "
            "submodule to its origin remote. Handles divergence via fetch + "
            "rebase, with agent-driven conflict resolution when needed. Use "
            "type='workspace' for the main workspace, or type='project' with "
            "target=<project-name> for a registered project.\n\n"
            "Optionally pass scrub_paths — a JSON-encoded string of file paths "
            '(e.g. \'["audio/large-file.ogg", "data/old.json"]\') — to '
            "permanently remove those paths from the entire git history of a "
            "project submodule. This is DESTRUCTIVE and IRREVERSIBLE — it "
            "rewrites all history and force-pushes to origin. Only works with "
            "type='project'. The string is JSON because the SDK MCP transport "
            "cannot reliably pass arrays. Example: push(type='project', "
            "target='my-pages', scrub_paths='[\"audio/large-file.ogg\", "
            '"data/old.json"]\').'
        ),
        PushArgs.model_json_schema(),
    )
    async def push(args: dict) -> dict:
        parsed = PushArgs.model_validate(args)
        scrub_paths_list: list[str] | None = None
        if parsed.scrub_paths is not None:
            try:
                scrub_paths_list = decode_json_string_array(parsed.scrub_paths, "scrub_paths")
            except ValueError as exc:
                return _error(f"Error: {exc}")
        return await handle_push(
            parsed.type,
            parsed.target,
            workspace_path,
            agent_defaults,
            scrub_paths_list,
        )

    @tool(
        "sync",
        (
            "Sync the workspace or a registered project submodule with its "
            "origin remote: pull (rebase on divergence, agent-driven conflict "
            "resolution) followed by push. Skips the push if the pull fails "
            "or is skipped due to uncommitted changes. Use type='workspace' "
            "for the main workspace, or type='project' with target=<project-name> "
            "for a registered project."
        ),
        SyncArgs.model_json_schema(),
    )
    async def sync(args: dict) -> dict:
        parsed = SyncArgs.model_validate(args)
        return await handle_sync(
            parsed.type,
            parsed.target,
            workspace_path,
            agent_defaults,
        )

    return create_sdk_mcp_server(
        name="git-tools",
        version="1.0.0",
        tools=[push, sync],
    )


# --- Destructive git deny patterns ---

# Regex patterns matched against each sub-command (after compound-split).
# Each pattern is anchored at the start of the sub-command (re.match semantics).
# These patterns target destructive / irrecoverable operations: push (use the
# push MCP tool instead), reset (rarely correct for an agent), checkout/restore
# of the current directory (discards working tree), clean, and mutating
# git remote subcommands. `git clone` and read-only git subcommands are
# deliberately not matched.
DESTRUCTIVE_GIT_DENY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^git\s+push\b"),
    re.compile(r"^git\s+reset\b"),
    re.compile(r"^git\s+(checkout|restore)\s+\.(\s|$)"),
    re.compile(r"^git\s+clean\b"),
    re.compile(r"^git\s+remote\s+(add|remove|rm|rename|set-url|set-head|set-branches|prune)\b"),
    re.compile(r"^git\s+filter-repo\b"),
]
