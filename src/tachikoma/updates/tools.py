"""MCP tools for update checking and applying."""

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


async def handle_apply_update(bus: EventBus) -> dict:
    """Run an upgrade and optionally signal restart.

    Side effect: dispatches RestartRequested on success.
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
    await bus.dispatch(RestartRequested())
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Upgrade successful: {result.old_version} → {result.new_version}\n"
                    "Restarting..."
                ),
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
        "Upgrade tachikoma-agent to the latest version using uv and restart the process. "
        "Only works for tool installs (not editable/development installs). "
        "The restart is automatic — warn the user before applying.",
        ApplyUpdateArgs.model_json_schema(),
    )
    async def apply_update(args: dict) -> dict:
        return await handle_apply_update(bus)

    return create_sdk_mcp_server(
        name="update-checker",
        version="1.0.0",
        tools=[check_updates, apply_update],
    )
