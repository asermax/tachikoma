"""Root pytest configuration and shared fixtures.

This file is auto-loaded by pytest and contains fixtures shared across test files.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.config import SettingsManager
from tachikoma.telegram import TelegramChannel


def _make_mock_coordinator() -> MagicMock:
    """Create a mock coordinator with critical defaults to prevent infinite loops.

    Must be used whenever a coordinator mock is injected into a TelegramChannel.
    ``has_deferred`` defaults to a truthy MagicMock, causing ``_drain_deferred_queue``'s
    while-loop to never terminate.

    ``_registry`` defaults to ``None`` so that registry-aware code paths
    (session routing, outgoing ID recording) skip gracefully.  Tests that
    need a real registry should use ``_make_channel_with_registry`` or set
    ``coordinator._registry`` to an ``AsyncMock`` explicitly.
    """
    coordinator = MagicMock()
    coordinator.has_deferred = False
    coordinator._registry = None
    return coordinator


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Pytest fixture variant of _make_mock_coordinator."""
    return _make_mock_coordinator()


def _make_mock_message(
    *,
    text: str = "hello",
    message_id: int = 100,
    entities: list | None = None,
    reply_to_message_id: int | None = None,
    reply_to_text: str | None = None,
    reply_to_caption: str | None = None,
) -> MagicMock:
    """Build a mock aiogram Message."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.entities = entities

    if reply_to_message_id is not None:
        reply = MagicMock()
        reply.message_id = reply_to_message_id
        reply.text = reply_to_text
        reply.caption = reply_to_caption
        msg.reply_to_message = reply
    else:
        msg.reply_to_message = None

    return msg


def _make_channel_with_registry(
    *,
    authorized_chat_id: int = 123,
    active_session_id: str = "session-current",
    lookup_result: str | None = None,
    inbound_reactions: bool = False,
) -> tuple:
    """Build a TelegramChannel with a mocked registry on the coordinator.

    Returns (channel, registry) tuple.
    """
    coordinator = _make_mock_coordinator()

    registry = AsyncMock()
    registry.get_active_session.return_value = MagicMock(id=active_session_id)
    registry.find_session_by_external_id.return_value = lookup_result
    coordinator._registry = registry

    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = authorized_chat_id
    settings.push_notifications = False
    settings.inbound_reactions = inbound_reactions

    with patch("tachikoma.telegram.Bot"):
        channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
        channel._TelegramChannel__coordinator = coordinator

    channel._bot = MagicMock()
    channel._bot.send_message = AsyncMock()
    channel._process_through_coordinator = AsyncMock()
    channel._drain_deferred_queue = AsyncMock()
    return channel, registry


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    """Create a SettingsManager with a temporary workspace path."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[workspace]\npath = "{tmp_path / "workspace"}"\n')
    return SettingsManager(config_path)
