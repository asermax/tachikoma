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
import json
import re
from pathlib import Path
from typing import Literal

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from loguru import logger
from pydantic import BaseModel, field_validator

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import (
    PUSH_RESULT,
    SYNC_RESULT,
    has_uncommitted_changes,
    run_git,
    run_git_capture,
    smart_pull,
    smart_push,
)

_log = logger.bind(component="git.tools")


# --- Arg models ---

TargetType = Literal["workspace", "project"]


class PushArgs(BaseModel):
    type: TargetType
    target: str | None = None
    scrub_paths: list[str] | None = None

    # Workaround: the Claude Agent SDK MCP transport occasionally serializes
    # array tool arguments as JSON-encoded strings. Decode defensively so the
    # tool keeps working without broadening the schema. See DES-006.
    @field_validator("scrub_paths", mode="before")
    @classmethod
    def _decode_scrub_paths(cls, v: object) -> object:
        if not isinstance(v, str):
            return v
        try:
            decoded = json.loads(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"scrub_paths must be a JSON-encoded array: {exc}") from exc
        if not isinstance(decoded, list):
            raise ValueError(
                f"scrub_paths JSON string must encode an array, got {type(decoded).__name__}"
            )
        return decoded


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

    result = await smart_push(resolved, "origin", "HEAD", agent_defaults)

    is_error = result in _PUSH_FAILURES

    _log.info(
        "push tool completed: target={desc} result={result}",
        desc=description,
        result=result,
    )

    return {
        "content": [
            {
                "type": "text",
                "text": f"push {description}: {result}",
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

    pull_result, _ = await smart_pull(resolved, "origin", "HEAD", agent_defaults)

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
            "Optionally pass scrub_paths (list of file paths) to permanently "
            "remove those paths from the entire git history of a project "
            "submodule. This is DESTRUCTIVE and IRREVERSIBLE — it rewrites "
            "all history and force-pushes to origin. Only works with "
            "type='project'. Example: push(type='project', target='my-pages', "
            "scrub_paths=['audio/large-file.ogg', 'data/old.json'])."
        ),
        PushArgs.model_json_schema(),
    )
    async def push(args: dict) -> dict:
        parsed = PushArgs.model_validate(args)
        return await handle_push(
            parsed.type,
            parsed.target,
            workspace_path,
            agent_defaults,
            parsed.scrub_paths,
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
