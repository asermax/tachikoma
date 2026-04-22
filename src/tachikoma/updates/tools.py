"""MCP tool for on-demand update checks."""

import asyncio

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel

from tachikoma.updates.checker import check_for_update


class CheckUpdatesArgs(BaseModel):
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


def create_update_tools_server() -> McpSdkServerConfig:
    """Create MCP server exposing the check_updates tool (DES-006)."""

    @tool(
        "check_updates",
        "Check whether a newer version of tachikoma-agent is available on PyPI. "
        "Returns the current version, latest version, and whether an update is available. "
        "This is a read-only check — it does not trigger notifications or updates.",
        CheckUpdatesArgs.model_json_schema(),
    )
    async def check_updates(args: dict) -> dict:
        return await handle_check_updates()

    return create_sdk_mcp_server(
        name="update-checker",
        version="1.0.0",
        tools=[check_updates],
    )
