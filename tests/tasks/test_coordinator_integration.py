"""Tests for coordinator task integration (DLT-010).

Tests for the new coordinator parameters: last_message_time and mcp_servers.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.coordinator import Coordinator


class TestCoordinatorLastMessageTime:
    """Tests for last_message_time tracking."""

    async def test_last_message_time_is_none_initially(self) -> None:
        """AC: last_message_time is None before any messages."""
        coordinator = Coordinator()

        assert coordinator.last_message_time is None


class TestCoordinatorMcpServers:
    """Tests for mcp_servers parameter."""

    async def test_mcp_servers_stored(self) -> None:
        """AC: mcp_servers is stored in coordinator."""
        from claude_agent_sdk import McpSdkServerConfig

        # Create a mock server config
        mock_server = MagicMock(spec=McpSdkServerConfig)
        coordinator = Coordinator(mcp_servers={"test": mock_server})

        # Verify it's stored
        assert coordinator._mcp_servers == {"test": mock_server}

    async def test_mcp_servers_none_by_default(self) -> None:
        """AC: mcp_servers defaults to None."""
        coordinator = Coordinator()

        assert coordinator._mcp_servers is None

    async def test_mcp_servers_empty_dict(self) -> None:
        """AC: mcp_servers can be an empty dict."""
        coordinator = Coordinator(mcp_servers={})

        assert coordinator._mcp_servers == {}
