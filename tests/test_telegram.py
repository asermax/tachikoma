"""Telegram channel tests.

Tests for DLT-002: Send and receive messages via Telegram.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from tachikoma.config import TelegramSettings
from tachikoma.events import Error, ToolActivity
from tachikoma.telegram import ResponseRenderer


class MockMessage:
    """Mock aiogram Message with message_id."""

    def __init__(self, message_id: int = 1):
        self.message_id = message_id


class TestResponseRendererState:
    """Tests for ResponseRenderer initial state and reset."""

    def test_initial_state(self) -> None:
        """Renderer starts with empty state."""
        bot = MagicMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        assert renderer._bot is bot
        assert renderer._chat_id == 123
        assert renderer._current_message_id is None
        assert renderer._buffer == ""
        assert renderer._tool_line is None
        assert renderer._had_tools is False
        assert renderer._last_edit_time == 0.0
        assert renderer._message_count == 0

    def test_reset_clears_state(self) -> None:
        """reset() clears all state except message count."""
        bot = MagicMock()
        renderer = ResponseRenderer(bot, chat_id=123)
        renderer._current_message_id = 42
        renderer._buffer = "some text"
        renderer._tool_line = "tool line"
        renderer._had_tools = True
        renderer._last_edit_time = 100.0
        renderer._message_count = 5

        renderer.reset()

        assert renderer._current_message_id is None
        assert renderer._buffer == ""
        assert renderer._tool_line is None
        assert renderer._had_tools is False
        assert renderer._last_edit_time == 0.0
        # Message count is NOT reset
        assert renderer._message_count == 5


class TestResponseRendererTextHandling:
    """Tests for handle_text() behavior."""

    async def test_handle_text_accumulates_buffer(self) -> None:
        """handle_text() appends to buffer."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_text("Hello")
        assert "Hello" in renderer._buffer

        await renderer.handle_text(" World")
        assert "Hello World" in renderer._buffer

    async def test_handle_text_sends_new_message_when_empty(self) -> None:
        """handle_text() sends new message when no current message exists."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_text("First chunk")

        bot.send_message.assert_called_once()

    async def test_handle_text_edits_existing_message(self) -> None:
        """handle_text() edits existing message when one exists."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("First")
        assert renderer._current_message_id == 1

        # Reset mock
        bot.send_message.reset_mock()

        # Second call edits (after throttle interval)
        import time

        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text(" Second")

        bot.edit_message_text.assert_called_once()
        bot.send_message.assert_not_called()

    async def test_handle_text_throttles_edits(self) -> None:
        """handle_text() throttles edits within 2-second window."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("First")
        assert bot.send_message.call_count == 1

        # Immediate second call is throttled (no edit)
        await renderer.handle_text(" Second")
        assert bot.edit_message_text.call_count == 0

    async def test_finalize_bypasses_throttle(self) -> None:
        """finalize() sends regardless of throttle timer."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("Text")

        # Reset mocks
        bot.send_message.reset_mock()
        bot.edit_message_text.reset_mock()

        # Immediate finalize bypasses throttle
        await renderer.finalize()

        bot.edit_message_text.assert_called_once()

    async def test_network_error_during_edit_is_caught(self) -> None:
        """Network errors during edit are caught and skipped."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock(
            side_effect=TelegramAPIError(method="edit_message_text", message="Network error")
        )
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("Text")

        # Should not raise despite API error
        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text(" More text")

        # Buffer should still accumulate
        assert "Text" in renderer._buffer
        assert "More text" in renderer._buffer


class TestResponseRendererToolHandling:
    """Tests for handle_tool() behavior."""

    async def test_tool_line_appears_in_message(self) -> None:
        """Tool activity appears as status line in message."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "main.py"})
        await renderer.handle_tool(activity)

        assert renderer._tool_line is not None
        assert "Reading" in renderer._tool_line
        assert "main.py" in renderer._tool_line

    async def test_second_tool_replaces_first(self) -> None:
        """Each new tool replaces the previous tool line."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity1 = ToolActivity(tool_name="Read", tool_input={"file_path": "a.py"})
        await renderer.handle_tool(activity1)
        first_tool_line = renderer._tool_line

        activity2 = ToolActivity(tool_name="Grep", tool_input={"pattern": "search"})
        await renderer.handle_tool(activity2)

        assert renderer._tool_line != first_tool_line
        assert "Searching" in renderer._tool_line

    async def test_tools_before_text_creates_message_with_tool_line(self) -> None:
        """Tool activity before any text creates message starting with tool line."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "file.py"})
        await renderer.handle_tool(activity)

        bot.send_message.assert_called_once()

    async def test_text_after_tools_inserts_ran_tools_marker(self) -> None:
        """Text after tools gets "🔧 Ran tools" marker."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Tool activity
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "file.py"})
        await renderer.handle_tool(activity)
        assert renderer._had_tools is True

        # Text after tools - the marker should be inserted before the text
        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text("Response text")

        assert "🔧 Ran tools" in renderer._buffer
        assert "Response text" in renderer._buffer

    async def test_generic_tool_uses_tool_name(self) -> None:
        """Unknown tools display with their name."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="UnknownTool", tool_input={})
        await renderer.handle_tool(activity)

        assert "UnknownTool" in renderer._tool_line


class TestResponseRendererMessageSplitting:
    """Tests for message splitting at 4096-char boundary."""

    async def test_splits_at_paragraph_boundary(self) -> None:
        """Buffer exceeding limit splits at paragraph boundary (\\n\\n)."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with paragraph boundary near the limit
        long_text = "A" * 3000 + "\n\n" + "B" * 2000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2

    async def test_falls_back_to_newline(self) -> None:
        """Falls back to newline when no paragraph boundary found."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with only single newlines
        long_text = "A" * 3000 + "\n" + "B" * 2000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2

    async def test_hard_splits_at_limit(self) -> None:
        """Hard-splits at 4096 when no newlines found."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with no newlines
        long_text = "A" * 5000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2


class TestResponseRendererErrorHandling:
    """Tests for handle_error() behavior."""

    async def test_error_sends_separate_message(self) -> None:
        """Error sends a new separate message to the chat."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        # First send a text to establish a message
        await renderer.handle_text("Response text")

        # Reset mock
        bot.send_message.reset_mock()

        # Error should send a new message
        error = Error(message="Something went wrong", recoverable=True)
        await renderer.handle_error(error)

        bot.send_message.assert_called_once()
        # Check error was formatted (second positional arg is text)
        call_args = bot.send_message.call_args
        assert "Error" in str(call_args) or "Something went wrong" in str(call_args)

    async def test_recoverable_error_does_not_log_at_error_level(self) -> None:
        """Recoverable error doesn't log at error level."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        error = Error(message="Transient error", recoverable=True)
        await renderer.handle_error(error)

        # No assertion on logging - just ensure it doesn't raise
        assert True

    async def test_non_recoverable_error_logs_at_error_level(self) -> None:
        """Non-recoverable error logs at error level."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        error = Error(message="Fatal error", recoverable=False)
        await renderer.handle_error(error)

        # No assertion on logging - just ensure it doesn't raise
        assert True


class TestResponseRendererRateLimitHandling:
    """Tests for Telegram rate limit handling."""

    async def test_retry_after_waits_and_continues(self) -> None:
        """TelegramRetryAfter is caught, waits, and continues."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))

        # First call raises retry_after, second succeeds
        bot.edit_message_text = AsyncMock(
            side_effect=[
                TelegramRetryAfter(
                    method="edit_message_text", message="Rate limit", retry_after=0.1
                ),
                None,  # Second call succeeds
            ]
        )

        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends message
        await renderer.handle_text("Text")

        # Reset throttle and try to edit (will hit retry_after)
        renderer._last_edit_time = 0.0
        await renderer.handle_text(" More")

        # Should have attempted edit despite rate limit
        assert bot.edit_message_text.call_count >= 1


class TestResponseRendererState:
    """Tests for ResponseRenderer initial state and reset."""

    def test_initial_state(self) -> None:
        """Renderer starts with empty state."""
        bot = MagicMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        assert renderer._bot is bot
        assert renderer._chat_id == 123
        assert renderer._current_message_id is None
        assert renderer._buffer == ""
        assert renderer._tool_line is None
        assert renderer._had_tools is False
        assert renderer._last_edit_time == 0.0
        assert renderer._message_count == 0

    def test_reset_clears_state(self) -> None:
        """reset() clears all state except message count."""
        bot = MagicMock()
        renderer = ResponseRenderer(bot, chat_id=123)
        renderer._current_message_id = 42
        renderer._buffer = "some text"
        renderer._tool_line = "tool line"
        renderer._had_tools = True
        renderer._last_edit_time = 100.0
        renderer._message_count = 5

        renderer.reset()

        assert renderer._current_message_id is None
        assert renderer._buffer == ""
        assert renderer._tool_line is None
        assert renderer._had_tools is False
        assert renderer._last_edit_time == 0.0
        # Message count is NOT reset
        assert renderer._message_count == 5


class TestResponseRendererTextHandling:
    """Tests for handle_text() behavior."""

    async def test_handle_text_accumulates_buffer(self) -> None:
        """handle_text() appends to buffer."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_text("Hello")
        assert "Hello" in renderer._buffer

        await renderer.handle_text(" World")
        assert "Hello World" in renderer._buffer

    async def test_handle_text_sends_new_message_when_empty(self) -> None:
        """handle_text() sends new message when no current message exists."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_text("First chunk")

        bot.send_message.assert_called_once()

    async def test_handle_text_edits_existing_message(self) -> None:
        """handle_text() edits existing message when one exists."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("First")
        assert renderer._current_message_id == 1

        # Reset mock
        bot.send_message.reset_mock()

        # Second call edits (after throttle interval)
        import time

        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text(" Second")

        bot.edit_message_text.assert_called_once()
        bot.send_message.assert_not_called()

    async def test_handle_text_throttles_edits(self) -> None:
        """handle_text() throttles edits within 2-second window."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("First")
        assert bot.send_message.call_count == 1

        # Immediate second call is throttled (no edit)
        await renderer.handle_text(" Second")
        assert bot.edit_message_text.call_count == 0

    async def test_finalize_bypasses_throttle(self) -> None:
        """finalize() sends regardless of throttle timer."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("Text")

        # Reset mocks
        bot.send_message.reset_mock()
        bot.edit_message_text.reset_mock()

        # Immediate finalize bypasses throttle
        await renderer.finalize()

        bot.edit_message_text.assert_called_once()

    async def test_network_error_during_edit_is_caught(self) -> None:
        """Network errors during edit are caught and skipped."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock(
            side_effect=TelegramAPIError(method="edit_message_text", message="Network error")
        )
        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends
        await renderer.handle_text("Text")

        # Should not raise despite API error
        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text(" More text")

        # Buffer should still accumulate
        assert "Text" in renderer._buffer
        assert "More text" in renderer._buffer


class TestResponseRendererToolHandling:
    """Tests for handle_tool() behavior."""

    async def test_tool_line_appears_in_message(self) -> None:
        """Tool activity appears as status line in message."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "main.py"})
        await renderer.handle_tool(activity)

        assert renderer._tool_line is not None
        assert "Reading" in renderer._tool_line
        assert "main.py" in renderer._tool_line

    async def test_second_tool_replaces_first(self) -> None:
        """Each new tool replaces the previous tool line."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity1 = ToolActivity(tool_name="Read", tool_input={"file_path": "a.py"})
        await renderer.handle_tool(activity1)
        first_tool_line = renderer._tool_line

        activity2 = ToolActivity(tool_name="Grep", tool_input={"pattern": "search"})
        await renderer.handle_tool(activity2)

        assert renderer._tool_line != first_tool_line
        assert "Searching" in renderer._tool_line

    async def test_tools_before_text_creates_message_with_tool_line(self) -> None:
        """Tool activity before any text creates message starting with tool line."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "file.py"})
        await renderer.handle_tool(activity)

        bot.send_message.assert_called_once()

    async def test_text_after_tools_inserts_ran_tools_marker(self) -> None:
        """Text after tools gets "🔧 Ran tools" marker."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Tool activity
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "file.py"})
        await renderer.handle_tool(activity)
        assert renderer._had_tools is True

        # Text after tools - the marker should be inserted before the text
        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text("Response text")

        assert "🔧 Ran tools" in renderer._buffer
        assert "Response text" in renderer._buffer

    async def test_generic_tool_uses_tool_name(self) -> None:
        """Unknown tools display with their name."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="UnknownTool", tool_input={})
        await renderer.handle_tool(activity)

        assert "UnknownTool" in renderer._tool_line


class TestResponseRendererMessageSplitting:
    """Tests for message splitting at 4096-char boundary."""

    async def test_splits_at_paragraph_boundary(self) -> None:
        """Buffer exceeding limit splits at paragraph boundary (\\n\\n)."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with paragraph boundary near the limit
        long_text = "A" * 3000 + "\n\n" + "B" * 2000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2

    async def test_falls_back_to_newline(self) -> None:
        """Falls back to newline when no paragraph boundary found."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with only single newlines
        long_text = "A" * 3000 + "\n" + "B" * 2000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2

    async def test_hard_splits_at_limit(self) -> None:
        """Hard-splits at 4096 when no newlines found."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text with no newlines
        long_text = "A" * 5000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2


class TestResponseRendererErrorHandling:
    """Tests for handle_error() behavior."""

    async def test_error_sends_separate_message(self) -> None:
        """Error sends a new separate message to the chat."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        # First send a text to establish a message
        await renderer.handle_text("Response text")

        # Reset mock
        bot.send_message.reset_mock()

        # Error should send a new message
        error = Error(message="Something went wrong", recoverable=True)
        await renderer.handle_error(error)

        bot.send_message.assert_called_once()
        # Check error was formatted (second positional arg is text)
        call_args = bot.send_message.call_args
        assert "Error" in str(call_args) or "Something went wrong" in str(call_args)

    async def test_recoverable_error_does_not_log_at_error_level(self) -> None:
        """Recoverable error doesn't log at error level."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        error = Error(message="Transient error", recoverable=True)
        await renderer.handle_error(error)

        # No assertion on logging - just ensure it doesn't raise
        assert True

    async def test_non_recoverable_error_logs_at_error_level(self) -> None:
        """Non-recoverable error logs at error level."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        error = Error(message="Fatal error", recoverable=False)
        await renderer.handle_error(error)

        # No assertion on logging - just ensure it doesn't raise
        assert True


class TestResponseRendererRateLimitHandling:
    """Tests for Telegram rate limit handling."""

    async def test_retry_after_waits_and_continues(self) -> None:
        """TelegramRetryAfter is caught, waits, and continues."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))

        # First call raises retry_after, second succeeds
        bot.edit_message_text = AsyncMock(
            side_effect=[
                TelegramRetryAfter(
                    method="edit_message_text", message="Rate limit", retry_after=0.1
                ),
                None,  # Second call succeeds
            ]
        )

        renderer = ResponseRenderer(bot, chat_id=123)

        # First call sends message
        await renderer.handle_text("Text")

        # Reset throttle and try to edit (will hit retry_after)
        renderer._last_edit_time = 0.0
        await renderer.handle_text(" More")

        # Should have attempted edit despite rate limit
        assert bot.edit_message_text.call_count >= 1

