"""MCP tools for update checking, applying, and restart."""

import asyncio

from bubus import EventBus
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel

from tachikoma.updates.apply import EDITABLE_ERROR, UpgradeResult, run_upgrade
from tachikoma.updates.checker import check_for_update
from tachikoma.updates.events import RestartRequested
from tachikoma.updates.rollback import write_rollback_marker


class CheckUpdatesArgs(BaseModel):
    pass


class ApplyUpdateArgs(BaseModel):
    pass


class RestartArgs(BaseModel):
    pass


async def handle_check_updates() -> dict:
    """Run an update check and return structured results. No side effects."""
    result = await asyncio.to_thread(check_for_update)
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Current version: {result.current_version}\n"
                    f"Latest version: {result.latest_version or 'unavailable'}\n"
                    f"Update available: {result.update_available}\n"
                    f"Latest is prerelease: {result.latest_is_prerelease}"
                ),
            }
        ],
    }


async def handle_apply_update() -> dict:
    """Run an upgrade and write the rollback marker on success.

    Does not restart the process — the agent must call ``restart`` separately
    to load the new code. The rollback marker stays coupled to the upgrade so
    a bare ``restart`` (no upgrade) does not trigger the rollback path.
    """
    result: UpgradeResult = await asyncio.to_thread(run_upgrade)

    if result.error == EDITABLE_ERROR:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Cannot update: tachikoma-agent is running from an "
                        "editable/development install. Self-update only works for "
                        "tool installs (uv tool install). To update, pull the "
                        "latest source and reinstall."
                    ),
                }
            ],
        }

    if result.error:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Upgrade failed:\n{result.error}",
                }
            ],
        }

    if result.already_up_to_date:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Already running the latest version: {result.old_version}",
                }
            ],
        }

    assert result.new_version is not None
    write_rollback_marker(result.old_version, result.new_version)
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Upgrade successful: {result.old_version} → {result.new_version}\n"
                    "Run `restart` to load the new version."
                ),
            }
        ],
    }


async def handle_restart(bus: EventBus) -> dict:
    """Dispatch a restart request on the bus."""
    await bus.dispatch(RestartRequested())
    return {
        "content": [
            {
                "type": "text",
                "text": "Restarting...",
            }
        ],
    }


def create_update_tools_server(bus: EventBus) -> McpSdkServerConfig:
    """Create MCP server exposing update tools (DES-006)."""

    @tool(
        "check_updates",
        "Check whether a newer version of tachikoma-agent is available on PyPI. "
        "Returns the current version, latest version, and whether an update is available. "
        "This is a read-only check — it does not trigger notifications or updates.",
        CheckUpdatesArgs.model_json_schema(),
    )
    async def check_updates(args: dict) -> dict:
        return await handle_check_updates()

    @tool(
        "apply_update",
        "Upgrade tachikoma-agent to the latest version using `uv tool install`. "
        "Only works for tool installs (not editable/development installs). "
        "Does NOT restart the process — call `restart` afterward to load the new code. "
        "Do NOT run `uv` upgrade commands directly in the shell — always use this tool. "
        "Do NOT tell the user the upgrade succeeded until the post-restart 'back online' "
        "notification appears (automatic rollback handles the failure case).",
        ApplyUpdateArgs.model_json_schema(),
    )
    async def apply_update(args: dict) -> dict:
        return await handle_apply_update()

    @tool(
        "restart",
        "Restart the tachikoma-agent process in place. Use after a successful "
        "`apply_update`, or to load new code after a manual `uv tool install --force`, "
        "or to resolve stale module/version cache issues. Warn the user before applying.",
        RestartArgs.model_json_schema(),
    )
    async def restart(args: dict) -> dict:
        return await handle_restart(bus)

    return create_sdk_mcp_server(
        name="update-checker",
        version="1.0.0",
        tools=[check_updates, apply_update, restart],
    )
