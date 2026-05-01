"""Tests for the pinning tool server (telegram/pinning.py).

Telegram message pinning.
"""

from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramAPIError

from tachikoma.telegram.pinning import (
    create_pinning_server,
    handle_pin_message,
    handle_unpin_message,
)


class TestHandlePinMessage:
    """Tests for the handle_pin_message handler."""

    async def test_pins_message_and_returns_id(self) -> None:
        """Successful pin returns message ID in response."""
        bot = MagicMock()
        bot.pin_chat_message = AsyncMock()

        result = await handle_pin_message(123, bot, 456)

        bot.pin_chat_message.assert_called_once_with(456, 123, disable_notification=False)
        assert result == {"content": [{"type": "text", "text": "Message pinned (ID: 123)"}]}

    async def test_returns_error_when_no_message_available(self) -> None:
        """None message_id produces an error response."""
        bot = MagicMock()

        result = await handle_pin_message(None, bot, 456)

        bot.pin_chat_message.assert_not_called()
        assert result["is_error"] is True
        assert "No message available to pin" in result["content"][0]["text"]

    async def test_returns_error_on_telegram_api_failure(self) -> None:
        """Telegram API error is returned as is_error response."""
        bot = MagicMock()
        bot.pin_chat_message = AsyncMock(
            side_effect=TelegramAPIError(
                method="pinChatMessage", message="Forbidden: bot can't pin messages"
            ),
        )

        result = await handle_pin_message(123, bot, 456)

        assert result["is_error"] is True
        assert "Failed to pin message" in result["content"][0]["text"]

    async def test_passes_disable_notification_false(self) -> None:
        """Pin call passes disable_notification=False so the pin triggers push notification."""
        bot = MagicMock()
        bot.pin_chat_message = AsyncMock()

        await handle_pin_message(123, bot, 456)

        call_kwargs = bot.pin_chat_message.call_args
        assert call_kwargs.kwargs.get("disable_notification") is False

    async def test_pin_already_pinned_succeeds(self) -> None:
        """Re-pinning an already-pinned message succeeds (idempotent)."""
        bot = MagicMock()
        bot.pin_chat_message = AsyncMock(return_value=True)

        result = await handle_pin_message(123, bot, 456)

        assert "is_error" not in result
        assert "Message pinned (ID: 123)" in result["content"][0]["text"]


class TestHandleUnpinMessage:
    """Tests for the handle_unpin_message handler."""

    async def test_unpins_message_and_returns_success(self) -> None:
        """Successful unpin returns success response with message ID."""
        bot = MagicMock()
        bot.unpin_chat_message = AsyncMock()

        result = await handle_unpin_message(123, bot, 456)

        bot.unpin_chat_message.assert_called_once_with(456, message_id=123)
        assert result == {"content": [{"type": "text", "text": "Message unpinned (ID: 123)"}]}

    async def test_returns_error_on_telegram_api_failure(self) -> None:
        """Telegram API error is returned as is_error response."""
        bot = MagicMock()
        bot.unpin_chat_message = AsyncMock(
            side_effect=TelegramAPIError(
                method="unpinChatMessage", message="Bad Request: message to unpin not found"
            ),
        )

        result = await handle_unpin_message(999, bot, 456)

        assert result["is_error"] is True
        assert "Failed to unpin message" in result["content"][0]["text"]

    async def test_unpin_non_pinned_succeeds(self) -> None:
        """Unpinning a non-pinned message succeeds (idempotent)."""
        bot = MagicMock()
        bot.unpin_chat_message = AsyncMock(return_value=True)

        result = await handle_unpin_message(123, bot, 456)

        assert "is_error" not in result
        assert "Message unpinned (ID: 123)" in result["content"][0]["text"]


class TestCreatePinningServer:
    """Tests for the pinning tool server factory."""

    def test_factory_returns_server_and_checker(self) -> None:
        """Factory returns a tuple of (server config, is_pinned checker)."""
        bot = MagicMock()

        def getter() -> int | None:
            return 123

        server, is_pinned = create_pinning_server(bot, 456, getter)

        assert server["name"] == "telegram-pinning"
        assert server["type"] == "sdk"
        assert is_pinned(123) is False
        assert is_pinned(42) is False
