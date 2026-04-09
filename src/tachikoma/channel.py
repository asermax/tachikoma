"""Channel protocol: defines the interface all communication channels implement.

Channels are the bridge between the user and the agent. The protocol defines
capability discovery methods (MCP tools, skills) and the main run loop.

Channels must explicitly inherit from Channel to receive the default no-op
implementations for get_mcp_servers() and get_skill_sources(). This follows
Python's Protocol explicit-subclassing pattern for defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from claude_agent_sdk.types import McpSdkServerConfig

if TYPE_CHECKING:
    from tachikoma.coordinator import Coordinator


@runtime_checkable
class Channel(Protocol):
    """Protocol for communication channels (REPL, Telegram, etc.).

    Channels declare capabilities via get_mcp_servers() and get_skill_sources(),
    then receive the coordinator in run() to process messages.
    """

    def get_mcp_servers(self) -> dict[str, McpSdkServerConfig]:
        """Return channel-specific MCP tool servers.

        Called during startup before the coordinator is created.
        Returned servers are merged into the coordinator's base servers.
        """
        return {}

    def get_skill_sources(self) -> list[Path]:
        """Return paths to channel-provided skill directories.

        Called during startup to register additional skills in the registry.
        """
        return []

    async def run(self, coordinator: Coordinator) -> None:
        """Start the channel's main loop.

        Args:
            coordinator: The coordinator to send messages through.
                         Set at run() time, not construction time.
        """
        ...
