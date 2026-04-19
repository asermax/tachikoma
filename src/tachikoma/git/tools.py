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

import re
from pathlib import Path
from typing import Literal

from claude_agent_sdk import create_sdk_mcp_server, tool
from claude_agent_sdk.types import McpSdkServerConfig
from loguru import logger
from pydantic import BaseModel

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.git.sync import (
    PUSH_RESULT,
    SYNC_RESULT,
    smart_pull,
    smart_push,
)

_log = logger.bind(component="git.tools")


# --- Arg models ---

TargetType = Literal["workspace", "project"]


class PushArgs(BaseModel):
    type: TargetType
    target: str | None = None


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


# --- Handlers ---


async def handle_push(
    type_: TargetType,
    target: str | None,
    workspace_path: Path,
    agent_defaults: AgentDefaults,
) -> dict:
    """Push the current branch of the resolved target to its ``origin`` remote.

    Uses :func:`smart_push` (fetch → divergence detection → rebase → push,
    with agent-driven conflict resolution on diverged branches).

    Args:
        type_: Target kind (``"workspace"`` or ``"project"``).
        target: Project name when ``type_ == "project"``.
        workspace_path: The workspace root directory.
        agent_defaults: Common SDK options (used for conflict-resolution
            agent spawning inside ``smart_push``).

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
            "target=<project-name> for a registered project."
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
]
