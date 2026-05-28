"""Telegram channel tests.

Send and receive messages via Telegram.
"""

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter

from tachikoma.buffer.events import BufferedDelivery
from tachikoma.events import Error, Result, ToolActivity
from tachikoma.message import TextMessage
from tachikoma.telegram import (
    TELEGRAM_TOOL_DISPLAY,
    TELEGRAM_TOOL_SUMMARY,
    ResponseRenderer,
    TelegramChannel,
    code_wrap,
)

from conftest import _make_mock_coordinator


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
        assert renderer._tool_activities == []
        assert renderer._last_edit_time == 0.0
        assert renderer._message_count == 0

    def test_reset_clears_state(self) -> None:
        """reset() clears all state except message count."""
        bot = MagicMock()
        renderer = ResponseRenderer(bot, chat_id=123)
        renderer._current_message_id = 42
        renderer._buffer = "some text"
        renderer._tool_line = "tool line"
        renderer._tool_activities = [ToolActivity(tool_name="Read", tool_input={})]
        renderer._last_edit_time = 100.0
        renderer._message_count = 5
        renderer._split_message_ids = [100, 101, 102]

        renderer.reset()

        assert renderer._current_message_id is None
        assert renderer._buffer == ""
        assert renderer._tool_line is None
        assert renderer._tool_activities == []
        assert renderer._last_edit_time == 0.0
        assert renderer._split_message_ids == []
        # Message count is NOT reset
        assert renderer._message_count == 5


class TestResponseRendererStatusHandling:
    """Tests for handle_status() behavior."""

    async def test_consecutive_status_edits_existing_message(self) -> None:
        """Second Status edits the existing message instead of creating a new one."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=42))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_status("Thinking...")
        await renderer.handle_status("Processing...")

        bot.send_message.assert_called_once()
        bot.edit_message_text.assert_called_once_with(
            "_Processing..._",
            chat_id=123,
            message_id=42,
            parse_mode="Markdown",
        )
        assert renderer._current_message_id == 42

    async def test_consecutive_status_does_not_increment_message_count(self) -> None:
        """Editing an existing status does not increment message count."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=42))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_status("Thinking...")
        await renderer.handle_status("Processing...")

        assert renderer._message_count == 1

    async def test_identical_status_message_not_modified_is_swallowed(self) -> None:
        """Telegram "message is not modified" BadRequest is swallowed by handle_status."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=42))
        bot.edit_message_text = AsyncMock(
            side_effect=TelegramBadRequest(
                method=MagicMock(),
                message=(
                    "Bad Request: message is not modified: specified new message "
                    "content and reply markup are exactly the same as a current "
                    "content and reply markup of the message"
                ),
            )
        )
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_status("Thinking...")
        # Second call edits with the same text — Telegram rejects, renderer must
        # swallow the BadRequest instead of raising or logging a traceback.
        await renderer.handle_status("Thinking...")

        bot.edit_message_text.assert_called_once()
        # state is unchanged — the status message id is preserved.
        assert renderer._current_message_id == 42


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

    async def test_text_after_tools_inserts_summary_marker(self) -> None:
        """Text after tools gets a summary marker with tool details."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Tool activity
        activity = ToolActivity(tool_name="Read", tool_input={"file_path": "file.py"})
        await renderer.handle_tool(activity)
        assert len(renderer._tool_activities) == 1

        # Text after tools - the summary marker should be inserted before the text
        renderer._last_edit_time = 0.0  # Reset throttle
        await renderer.handle_text("Response text")

        assert "🔧" in renderer._buffer
        assert "Reading `file.py`" in renderer._buffer  # Summary includes code-wrapped tool details
        assert "Response text" in renderer._buffer

    async def test_multiple_tool_text_cycles_insert_multiple_markers(self) -> None:
        """Each tool→text transition inserts its own summary marker (AC1)."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First cycle: tools → text
        await renderer.handle_tool(ToolActivity(tool_name="Read", tool_input={"file_path": "a.py"}))
        renderer._last_edit_time = 0.0
        await renderer.handle_text("First response")

        # Second cycle: tools → text
        await renderer.handle_tool(ToolActivity(tool_name="Edit", tool_input={"file_path": "b.py"}))
        renderer._last_edit_time = 0.0
        await renderer.handle_text("Second response")

        assert renderer._buffer.count("🔧") == 2

    async def test_consecutive_tool_batches_without_text_produce_single_marker(self) -> None:
        """Consecutive tools without text between them produce one summary (AC4)."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # First cycle: tools → text
        await renderer.handle_tool(ToolActivity(tool_name="Read", tool_input={"file_path": "a.py"}))
        renderer._last_edit_time = 0.0
        await renderer.handle_text("First response")

        # Second cycle: two tool batches without text, then text
        await renderer.handle_tool(ToolActivity(tool_name="Grep", tool_input={"pattern": "foo"}))
        await renderer.handle_tool(ToolActivity(tool_name="Edit", tool_input={"file_path": "b.py"}))
        renderer._last_edit_time = 0.0
        await renderer.handle_text("Second response")

        assert renderer._buffer.count("🔧") == 2

    async def test_generic_tool_uses_tool_name(self) -> None:
        """Unknown tools display with their name."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="UnknownTool", tool_input={})
        await renderer.handle_tool(activity)

        assert "UnknownTool" in renderer._tool_line


class TestResponseRendererMessageSplitting:
    """Tests for message splitting via post-conversion UTF-16 check."""

    async def test_splits_long_text_into_multiple_messages(self) -> None:
        """Text exceeding 4096 UTF-16 code units after convert() is split."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Create text that exceeds 4096 chars (plain ASCII is 1:1 UTF-16)
        long_text = "A" * 3000 + "\n\n" + "B" * 2000
        renderer._buffer = long_text

        await renderer.finalize()

        # Should have sent multiple messages
        assert bot.send_message.call_count >= 2

    async def test_splits_when_convert_expands_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Text under raw limit but over 4096 UTF-16 after convert() gets split."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Short raw markdown that will "expand" after convert()
        renderer._buffer = "short text"

        # Mock convert to return text exceeding 4096 UTF-16 code units
        expanded_text = "X" * 5000
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: (expanded_text, []),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.utf16_len",
            lambda t: len(t),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.split_entities",
            lambda text, entities, limit: [(text[:limit], []), (text[limit:], [])],
        )

        await renderer.finalize()

        # First chunk edits nothing (no current message), so both are send_message
        assert bot.send_message.call_count == 2

    async def test_no_split_when_under_limit(self) -> None:
        """Text under 4096 UTF-16 code units is sent as a single message."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123)

        renderer._buffer = "Short text that fits easily"

        await renderer.finalize()

        assert bot.send_message.call_count == 1

    async def test_resplit_reuses_existing_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-split edits existing tracked messages instead of sending new ones (AC1)."""
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.send_message = AsyncMock(
            side_effect=[
                MockMessage(message_id=200),
                MockMessage(message_id=201),
            ]
        )
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Mock splitting to produce 2 chunks
        chunk_a = ("Part A", [])
        chunk_b = ("Part B", [])
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: ("Part A\n\nPart B", []),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.utf16_len",
            lambda t: 5000,
        )
        monkeypatch.setattr(
            "tachikoma.telegram.split_entities",
            lambda text, entities, limit: [chunk_a, chunk_b],
        )

        renderer._current_message_id = 100
        renderer._buffer = "Part A\n\nPart B"

        # First split: should edit streaming msg (100) + send new (200)
        await renderer._flush(force=True)
        assert renderer._split_message_ids == [100, 200]

        # Reset mocks
        bot.edit_message_text.reset_mock()
        bot.send_message.reset_mock()

        # Second split (re-split): should edit 100 and 200 in-place
        renderer._buffer = "Part A updated\n\nPart B updated"
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: ("Part A updated\n\nPart B updated", []),
        )

        await renderer._flush(force=True)

        # Should edit both tracked messages, not send new ones
        assert bot.edit_message_text.call_count == 2
        bot.send_message.assert_not_called()
        assert renderer._split_message_ids == [100, 200]

    async def test_resplit_deletes_excess_messages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Re-split producing fewer chunks deletes excess messages (AC2)."""
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Simulate state after a 3-message split
        renderer._current_message_id = 300
        renderer._split_message_ids = [100, 200, 300]
        renderer._buffer = "Content"

        # Mock to produce only 2 chunks now
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: ("X" * 5000, []),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.utf16_len",
            lambda t: 5000,
        )
        monkeypatch.setattr(
            "tachikoma.telegram.split_entities",
            lambda text, entities, limit: [("Chunk 0", []), ("Chunk 1", [])],
        )

        await renderer._flush(force=True)

        # Should edit 100 and 200, delete excess 300
        assert bot.edit_message_text.call_count == 2
        bot.delete_message.assert_called_once_with(
            chat_id=123,
            message_id=300,
        )
        assert renderer._split_message_ids == [100, 200]

    async def test_shrink_to_unsplit_deletes_excess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Content was split but now fits in one message — excess deleted (shrink-to-unsplit)."""
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Simulate state after a 3-message split
        renderer._current_message_id = 300
        renderer._split_message_ids = [100, 200, 300]
        renderer._buffer = "Short text"

        # Mock to fit in a single message
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: ("Short text", []),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.utf16_len",
            lambda t: 100,
        )

        await renderer._flush(force=True)

        # Should edit first message (100) and delete excess (200, 300)
        assert bot.edit_message_text.call_count == 1
        assert bot.delete_message.call_count == 2
        assert renderer._split_message_ids == []
        assert renderer._current_message_id == 100

    async def test_edit_failure_continues_gracefully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Failed edit on tracked split message is logged and skipped (AC5)."""
        bot = MagicMock()
        bot.edit_message_text = AsyncMock(
            side_effect=TelegramAPIError(method="edit_message_text", message="Failed")
        )
        bot.send_message = AsyncMock(
            side_effect=[
                MockMessage(message_id=200),
                MockMessage(message_id=201),
            ]
        )
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Simulate state with 2 tracked split messages
        renderer._current_message_id = 200
        renderer._split_message_ids = [100, 200]
        renderer._buffer = "Updated content"

        # Mock to produce 2 chunks
        monkeypatch.setattr(
            "tachikoma.telegram.convert",
            lambda _: ("Chunk 0\n\nChunk 1", []),
        )
        monkeypatch.setattr(
            "tachikoma.telegram.utf16_len",
            lambda t: 5000,
        )
        monkeypatch.setattr(
            "tachikoma.telegram.split_entities",
            lambda text, entities, limit: [("Chunk 0", []), ("Chunk 1", [])],
        )

        # Should not raise despite edit failures
        await renderer._flush(force=True)

        # Both edits attempted (and failed), but no crash
        assert bot.edit_message_text.call_count == 2
        # IDs should still be tracked (best-effort)
        assert renderer._split_message_ids == [100, 200]


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


class TestResponseRendererSilentSending:
    """Tests for silent message sending (disable_notification=True)."""

    async def test_status_sends_silently_when_enabled(self) -> None:
        """Status messages sent silently when push_notifications=True."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        await renderer.handle_status("Thinking...")

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs.get("disable_notification") is True

    async def test_text_sends_silently_when_enabled(self) -> None:
        """Text messages sent silently when push_notifications=True."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        await renderer.handle_text("Response text")

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs.get("disable_notification") is True

    async def test_silent_disabled_by_default(self) -> None:
        """Silent sending disabled by default (push_notifications=False)."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=False)

        await renderer.handle_text("Response text")

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs.get("disable_notification") is False

    async def test_error_silent_when_content_streamed(self) -> None:
        """Error sent silently when content already streamed and push enabled."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        # Stream content first (creates current_message_id)
        await renderer.handle_text("Some content")

        bot.send_message.reset_mock()

        # Error should be silent
        error = Error(message="Something went wrong", recoverable=True)
        await renderer.handle_error(error)

        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs.get("disable_notification") is True

    async def test_error_not_silent_when_no_content(self) -> None:
        """Error NOT silent when no content streamed yet."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        # No content streamed - error should NOT be silent
        error = Error(message="Early failure", recoverable=True)
        await renderer.handle_error(error)

        call_kwargs = bot.send_message.call_args.kwargs
        assert call_kwargs.get("disable_notification") is False


class TestResponseRendererNotify:
    """Tests for the notify() copy+delete push notification trigger."""

    async def test_notify_noop_when_disabled(self) -> None:
        """notify() is no-op when push_notifications=False."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=False)
        renderer._current_message_id = 1

        await renderer.notify()

        bot.copy_message.assert_not_called()

    async def test_notify_noop_when_no_message(self) -> None:
        """notify() is no-op when no message was sent."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        await renderer.notify()

        bot.copy_message.assert_not_called()

    async def test_notify_copies_and_deletes(self) -> None:
        """notify() copies message then deletes original."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_called_once_with(
            chat_id=123,
            from_chat_id=123,
            message_id=42,
        )
        bot.delete_message.assert_called_once_with(
            chat_id=123,
            message_id=42,
        )

    async def test_notify_skips_delete_on_copy_failure(self) -> None:
        """notify() skips delete if copy fails (preserves original)."""
        bot = MagicMock()
        bot.copy_message = AsyncMock(
            side_effect=TelegramAPIError(method="copy_message", message="Failed")
        )
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_called_once()
        bot.delete_message.assert_not_called()

    async def test_notify_accepts_duplicate_on_delete_failure(self) -> None:
        """notify() retries delete 3 times and accepts duplicate on final failure."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock(
            side_effect=TelegramAPIError(method="delete_message", message="Failed")
        )
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        # Should not raise
        await renderer.notify()

        bot.copy_message.assert_called_once()
        # Delete is retried 3 times
        assert bot.delete_message.call_count == 3

    async def test_notify_skips_copy_delete_for_pinned_message(self) -> None:
        """notify() skips entire copy+delete when message is pinned."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()

        pinned_ids = {42}
        renderer = ResponseRenderer(
            bot,
            chat_id=123,
            push_notifications=True,
            is_pinned=lambda mid: mid in pinned_ids,
        )
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_not_called()
        bot.delete_message.assert_not_called()

    async def test_notify_copy_deletes_after_unpin(self) -> None:
        """notify() performs full copy+delete when message was unpinned."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()

        pinned_ids: set[int] = set()
        renderer = ResponseRenderer(
            bot,
            chat_id=123,
            push_notifications=True,
            is_pinned=lambda mid: mid in pinned_ids,
        )
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_called_once()
        bot.delete_message.assert_called_once()

    async def test_notify_no_checker_same_as_unpinned(self) -> None:
        """notify() with no is_pinned checker performs full copy+delete."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_called_once()
        bot.delete_message.assert_called_once()


class TestTelegramChannelStdinShutdown:
    """Tests for 'q' keypress graceful shutdown in TelegramChannel.run()."""

    def _make_channel(self) -> TelegramChannel:
        """Build a TelegramChannel with mocked dependencies."""
        settings = MagicMock()
        settings.bot_token = "123456:ABCdef"
        settings.authorized_chat_id = 123

        channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))

        # Replace dispatcher and bot with mocks
        channel._dispatcher = MagicMock()
        channel._dispatcher.start_polling = AsyncMock()
        channel._dispatcher.stop_polling = AsyncMock()
        channel._dispatcher.include_router = MagicMock()
        channel._dispatcher.shutdown = MagicMock()
        channel._bot = MagicMock()
        channel._bot.set_my_commands = AsyncMock()

        return channel

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_q_keypress_stops_polling(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """Pressing 'q' triggers stop_polling()."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_termios.tcgetattr.return_value = [0, 0, 0, 0]
        mock_termios.TCSADRAIN = 1

        # Capture the callback registered with add_reader
        captured_callback = None
        loop = MagicMock()

        def capture_add_reader(fd: int, callback: object) -> None:
            nonlocal captured_callback
            captured_callback = callback

        loop.add_reader.side_effect = capture_add_reader
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        # Simulate 'q' keypress via the captured callback
        assert captured_callback is not None
        mock_sys.stdin.read.return_value = "q"

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()
            mock_ensure.assert_called_once()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_q_uppercase_stops_polling(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """Pressing 'Q' (uppercase) also triggers stop_polling()."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_termios.tcgetattr.return_value = [0, 0, 0, 0]
        mock_termios.TCSADRAIN = 1

        captured_callback = None
        loop = MagicMock()

        def capture_add_reader(fd: int, callback: object) -> None:
            nonlocal captured_callback
            captured_callback = callback

        loop.add_reader.side_effect = capture_add_reader
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        assert captured_callback is not None
        mock_sys.stdin.read.return_value = "Q"

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()
            mock_ensure.assert_called_once()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_non_q_keypress_does_not_stop(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """Pressing a non-q key does not trigger shutdown."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_termios.tcgetattr.return_value = [0, 0, 0, 0]
        mock_termios.TCSADRAIN = 1

        captured_callback = None
        loop = MagicMock()

        def capture_add_reader(fd: int, callback: object) -> None:
            nonlocal captured_callback
            captured_callback = callback

        loop.add_reader.side_effect = capture_add_reader
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        assert captured_callback is not None
        mock_sys.stdin.read.return_value = "x"

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()
            mock_ensure.assert_not_called()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_stdin_not_tty_skips_reader(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """Non-TTY stdin skips reader and terminal setup."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = False

        loop = MagicMock()
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        loop.add_reader.assert_not_called()
        mock_termios.tcgetattr.assert_not_called()
        mock_tty.setcbreak.assert_not_called()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_terminal_restored_on_exit(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """Terminal settings are restored in the finally block."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        original_attrs = [1, 2, 3, 4]
        mock_termios.tcgetattr.return_value = original_attrs
        mock_termios.TCSADRAIN = 1

        loop = MagicMock()
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()
        loop.add_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        # Terminal settings restored
        mock_termios.tcsetattr.assert_called_once_with(0, 1, original_attrs)

        # Reader cleaned up
        loop.remove_reader.assert_called_once_with(0)

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_eof_on_stdin_removes_reader(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """EOF on stdin removes the reader to prevent busy-loop spin."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = True
        mock_sys.stdin.fileno.return_value = 0
        mock_termios.tcgetattr.return_value = [0, 0, 0, 0]
        mock_termios.TCSADRAIN = 1

        captured_callback = None
        loop = MagicMock()

        def capture_add_reader(fd: int, callback: object) -> None:
            nonlocal captured_callback
            captured_callback = callback

        loop.add_reader.side_effect = capture_add_reader
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        assert captured_callback is not None
        mock_sys.stdin.read.return_value = ""

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()

            # Should NOT trigger shutdown
            mock_ensure.assert_not_called()

            # Should remove reader to prevent spin
            loop.remove_reader.assert_called_with(0)


class TestProcessThroughCoordinatorNotify:
    """Tests for notify() integration in _process_through_coordinator."""

    async def test_notify_called_after_result_when_push_enabled(self) -> None:
        """notify() called after Result event when push_notifications=True."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()

        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)

        # Simulate text chunk then finalize + notify
        await renderer.handle_text("Hello")
        await renderer.finalize()
        await renderer.notify()

        # copy_message called means notify() worked
        bot.copy_message.assert_called_once()

    async def test_notify_not_called_when_push_disabled(self) -> None:
        """notify() NOT called when push_notifications=False."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock()

        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=False)

        await renderer.handle_text("Hello")
        await renderer.finalize()
        await renderer.notify()

        bot.copy_message.assert_not_called()


class TestProcessThroughCoordinatorDrain:
    """Tests for _process_through_coordinator after drain loop removal.

    The coordinator's re-queue loop handles leftover messages internally,
    so the channel just calls send_message() once.
    """

    async def test_calls_send_message_exactly_once(self) -> None:
        """_process_through_coordinator calls send_message() once, not in a loop."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()

        coordinator = _make_mock_coordinator()
        coordinator.has_pending_messages = True

        send_call_count = 0

        async def _fake_send_message():
            nonlocal send_call_count
            send_call_count += 1
            yield Result(session_id="sdk-1", total_cost_usd=0.0)

        coordinator.send_message = _fake_send_message

        settings = MagicMock()
        settings.authorized_chat_id = 123
        settings.push_notifications = False

        with patch("tachikoma.telegram.Bot"):
            channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
            channel._TelegramChannel__coordinator = coordinator
        channel._bot = bot

        await channel._process_through_coordinator()

        # Single call to send_message — no drain loop
        assert send_call_count == 1

    async def test_renders_continuous_stream_without_drain_loop(self) -> None:
        """Events from multiple exchanges in one generator are processed as a stream."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()

        coordinator = _make_mock_coordinator()
        coordinator.has_pending_messages = True

        async def _multi_exchange_send_message():
            # Simulates coordinator re-queue: two Result events in one generator
            yield Result(session_id="sdk-1", total_cost_usd=0.0)
            yield Result(session_id="sdk-2", total_cost_usd=0.0)

        coordinator.send_message = _multi_exchange_send_message

        settings = MagicMock()
        settings.authorized_chat_id = 123
        settings.push_notifications = False

        with patch("tachikoma.telegram.Bot"):
            channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
            channel._TelegramChannel__coordinator = coordinator
        channel._bot = bot

        # Should complete without error — no drain loop needed
        await channel._process_through_coordinator()


class TestCodeWrap:
    """Tests for code_wrap() utility."""

    def test_normal_text_single_backtick(self) -> None:
        """Normal text wrapped in single backticks."""
        assert code_wrap("hello") == "`hello`"

    def test_text_with_backtick_double_backtick(self) -> None:
        """Text containing backtick uses double backticks with space padding."""
        assert code_wrap("echo `date`") == "`` echo `date` ``"

    def test_text_starting_with_backtick(self) -> None:
        """Text starting with backtick gets space padding."""
        assert code_wrap("`x`") == "`` `x` ``"

    def test_text_ending_with_backtick(self) -> None:
        """Text ending with backtick gets space padding."""
        assert code_wrap("foo`") == "`` foo` ``"

    def test_fallback_placeholder(self) -> None:
        """Fallback placeholder '...' wrapped in single backticks."""
        assert code_wrap("...") == "`...`"


class TestTelegramToolDisplay:
    """Tests for TELEGRAM_TOOL_DISPLAY formatters."""

    def test_read_code_wraps_path(self) -> None:
        """Read wraps file_path in backticks."""
        result = TELEGRAM_TOOL_DISPLAY["Read"]({"file_path": "/src/main.py"})
        assert result == "Reading `/src/main.py`"

    def test_grep_code_wraps_pattern(self) -> None:
        """Grep wraps pattern in backticks, no single quotes."""
        result = TELEGRAM_TOOL_DISPLAY["Grep"]({"pattern": "git.*push"})
        assert result == "Searching for `git.*push`"
        assert "'" not in result

    def test_glob_code_wraps_pattern(self) -> None:
        """Glob wraps pattern in backticks."""
        result = TELEGRAM_TOOL_DISPLAY["Glob"]({"pattern": "**/*.ts"})
        assert result == "Globbing `**/*.ts`"

    def test_bash_with_description_plain_text(self) -> None:
        """Bash with description shows plain description, no code wrapping."""
        result = TELEGRAM_TOOL_DISPLAY["Bash"]({"description": "install deps"})
        assert result == "install deps"

    def test_bash_with_command_only(self) -> None:
        """Bash with command only shows 'Running: ' + code-wrapped command."""
        result = TELEGRAM_TOOL_DISPLAY["Bash"]({"command": "ls -la *.py"})
        assert result == "Running: `ls -la *.py`"

    def test_edit_code_wraps_path(self) -> None:
        """Edit wraps file_path in backticks."""
        result = TELEGRAM_TOOL_DISPLAY["Edit"]({"file_path": "test_utils.py"})
        assert result == "Editing `test_utils.py`"

    def test_write_code_wraps_path(self) -> None:
        """Write wraps file_path in backticks."""
        result = TELEGRAM_TOOL_DISPLAY["Write"]({"file_path": "output.json"})
        assert result == "Writing `output.json`"

    def test_agent_no_code_wrap(self) -> None:
        """Agent description is NOT code-wrapped."""
        result = TELEGRAM_TOOL_DISPLAY["Agent"]({"description": "research codebase"})
        assert result == "Agent: research codebase"
        assert "`" not in result

    def test_tool_search_no_code_wrap(self) -> None:
        """ToolSearch query is NOT code-wrapped."""
        result = TELEGRAM_TOOL_DISPLAY["ToolSearch"]({"query": "git"})
        assert result == "Searching tools: git"
        assert "`" not in result

    def test_read_with_dunder_path(self) -> None:
        """Read with dunder filename is properly code-wrapped."""
        result = TELEGRAM_TOOL_DISPLAY["Read"]({"file_path": "/src/__init__.py"})
        assert "`/src/__init__.py`" in result

    def test_fallback_placeholder_code_wrapped(self) -> None:
        """Missing key fallback '...' is still code-wrapped."""
        result = TELEGRAM_TOOL_DISPLAY["Grep"]({})
        assert "`...`" in result


class TestTelegramToolSummary:
    """Tests for TELEGRAM_TOOL_SUMMARY formatters."""

    def test_grep_no_quotes_code_wrapped(self) -> None:
        """Grep summary: code-wrapped, no single quotes (R6)."""
        result = TELEGRAM_TOOL_SUMMARY["Grep"]({"pattern": "config"})
        assert result == "searching for `config`"
        assert "'" not in result

    def test_glob_no_quotes_code_wrapped(self) -> None:
        """Glob summary: code-wrapped, no single quotes (R6)."""
        result = TELEGRAM_TOOL_SUMMARY["Glob"]({"pattern": "*.py"})
        assert result == "globbing `*.py`"
        assert "'" not in result

    def test_read_code_wraps_basename(self) -> None:
        """Read summary wraps basename in backticks."""
        result = TELEGRAM_TOOL_SUMMARY["Read"]({"file_path": "/src/main.py"})
        assert result == "reading `main.py`"

    def test_bash_description_plain_text(self) -> None:
        """Bash summary shows lowercased description as plain text."""
        result = TELEGRAM_TOOL_SUMMARY["Bash"]({"description": "Run tests"})
        assert result == "run tests"

    def test_agent_no_code_wrap(self) -> None:
        """Agent summary is NOT code-wrapped (same as shared)."""
        result = TELEGRAM_TOOL_SUMMARY["Agent"]({"description": "research"})
        assert result == "agent: research"
        assert "`" not in result


class TestRendererTelegramFormatting:
    """Integration tests for Telegram-specific formatting in ResponseRenderer."""

    async def test_grep_tool_line_code_wraps_pattern(self) -> None:
        """Grep tool line in Telegram has backtick-wrapped pattern."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Grep", tool_input={"pattern": "**/*.ts"})
        await renderer.handle_tool(activity)

        assert "`**/*.ts`" in renderer._tool_line

    async def test_summary_after_tools_code_wraps_arguments(self) -> None:
        """Summary after tools has code-wrapped arguments in Telegram."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        await renderer.handle_tool(ToolActivity(tool_name="Read", tool_input={"file_path": "a.py"}))
        renderer._last_edit_time = 0.0
        await renderer.handle_text("Response")

        assert "Reading `a.py`" in renderer._buffer

    async def test_agent_tool_line_no_code_wrap(self) -> None:
        """Agent tool line in Telegram does NOT code-wrap description."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(tool_name="Agent", tool_input={"description": "research patterns"})
        await renderer.handle_tool(activity)

        assert "`" not in renderer._tool_line
        assert "research patterns" in renderer._tool_line

    async def test_bash_with_description_plain_text(self) -> None:
        """Bash tool line with description shows plain text, no code wrapping."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        activity = ToolActivity(
            tool_name="Bash",
            tool_input={"description": "install dependencies"},
        )
        await renderer.handle_tool(activity)

        assert "install dependencies" in renderer._tool_line
        assert "`" not in renderer._tool_line


class TestResponseRendererSanitization:
    """Tests for surrogate sanitization at the Telegram boundary."""

    async def test_handle_error_sanitizes_surrogates(self) -> None:
        """Error messages with surrogates are sanitized before sending."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        renderer = ResponseRenderer(bot, chat_id=123)

        error = Error(message="bad\ud83edata", recoverable=True)
        await renderer.handle_error(error)

        call_args = bot.send_message.call_args
        sent_text = call_args[0][1]
        clean = sent_text.encode("utf-8", errors="surrogatepass")
        assert "\ud83e" not in clean.decode("utf-8", errors="ignore")

    async def test_flush_sanitizes_surrogates_from_tool_labels(self) -> None:
        """Tool labels with surrogates in tool_input are sanitized before sending."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage(message_id=1))
        bot.edit_message_text = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123)

        # Tool with surrogate in file_path
        activity = ToolActivity(
            tool_name="Read",
            tool_input={"file_path": "path/with/\ud83e/surrogate"},
        )
        await renderer.handle_tool(activity)
        renderer._last_edit_time = 0.0

        # Text after tool triggers flush
        await renderer.handle_text("Response")

        # Verify no surrogates in any sent text
        for call in bot.send_message.call_args_list:
            sent_text = call[0][1]
            clean = sent_text.encode("utf-8", errors="surrogatepass")
            assert "\ud83e" not in clean.decode("utf-8", errors="ignore")

    async def test_top_level_exception_sanitizes_surrogates(self) -> None:
        """Top-level exception handler sanitizes surrogates in error message."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MockMessage())
        coordinator = _make_mock_coordinator()
        coordinator.has_pending_messages = True

        settings = MagicMock()
        settings.authorized_chat_id = 123
        settings.push_notifications = False

        with patch("tachikoma.telegram.Bot"):
            channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
            channel._TelegramChannel__coordinator = coordinator
        channel._bot = bot

        # Mock coordinator to raise exception with surrogate
        async def failing_generator():
            raise RuntimeError("crash\ud83efail")
            yield  # makes this an async generator

        coordinator.send_message = failing_generator

        await channel._process_through_coordinator()

        # Find the error message call
        error_calls = [c for c in bot.send_message.call_args_list if "crash" in str(c)]
        assert len(error_calls) >= 1
        sent_text = error_calls[0][0][1]
        clean = sent_text.encode("utf-8", errors="surrogatepass")
        assert "\ud83e" not in clean.decode("utf-8", errors="ignore")


class TestHandleMedia:
    """Tests for _handle_media handler in TelegramChannel."""

    def _make_channel(self) -> TelegramChannel:
        """Build a TelegramChannel with mocked dependencies."""
        coordinator = _make_mock_coordinator()
        settings = MagicMock()
        settings.bot_token = "123456:ABCdef"
        settings.authorized_chat_id = 123
        settings.push_notifications = False

        with patch("tachikoma.telegram.Bot"):
            channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
            channel._TelegramChannel__coordinator = coordinator

        channel._bot = MagicMock()
        return channel

    def _make_media_message(self, **fields: MagicMock) -> MagicMock:
        """Create a mock message with media fields."""
        msg = MagicMock()
        msg.photo = None
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.sticker = None
        msg.video = None
        msg.video_note = None
        msg.animation = None
        msg.caption = None
        for field, value in fields.items():
            setattr(msg, field, value)
        return msg

    async def test_photo_happy_path(self) -> None:
        """Photo message downloads, describes, and enqueues."""
        channel = self._make_channel()
        channel._bot.download = AsyncMock(return_value=None)

        # Create photo media mock
        photo = MagicMock()
        photo.file_id = "photo_123"
        photo.width = 1280
        photo.height = 720
        photo.file_size = 250_000
        photo.file_name = None

        msg = self._make_media_message(photo=[photo])
        msg.caption = "Check this out"

        channel._process_through_coordinator = AsyncMock()

        await channel._handle_media(msg)

        # Should have enqueued a description
        enqueued_msg = channel._coordinator.enqueue.call_args[0][0]
        assert "Photo" in enqueued_msg.text
        assert "1280 × 720" in enqueued_msg.text
        assert "/tmp/tachikoma-media/" in enqueued_msg.text
        assert 'The user said: "Check this out"' in enqueued_msg.text

        # Should have called process_through_coordinator
        channel._process_through_coordinator.assert_called_once()

    async def test_no_caption(self) -> None:
        """Media without caption omits caption line."""
        channel = self._make_channel()
        channel._bot.download = AsyncMock(return_value=None)

        voice = MagicMock()
        voice.file_id = "voice_123"
        voice.duration = 12
        voice.mime_type = "audio/ogg"
        voice.file_size = 50_000
        voice.file_name = None

        msg = self._make_media_message(voice=voice)
        msg.caption = None

        channel._process_through_coordinator = AsyncMock()

        await channel._handle_media(msg)

        enqueued_msg = channel._coordinator.enqueue.call_args[0][0]
        assert "Voice message" in enqueued_msg.text
        assert "The user said" not in enqueued_msg.text

    async def test_file_too_large(self) -> None:
        """File too large sends error message, does not enqueue."""
        channel = self._make_channel()
        channel._bot.send_message = AsyncMock(return_value=MockMessage())

        photo = MagicMock()
        photo.file_id = "big_photo"
        photo.width = 4000
        photo.height = 3000
        photo.file_size = 25 * 1024 * 1024  # 25 MB
        photo.file_name = None

        msg = self._make_media_message(photo=[photo])

        await channel._handle_media(msg)

        # Error message sent to user
        channel._bot.send_message.assert_called_once()
        sent_text = channel._bot.send_message.call_args[0][1]
        assert "too large" in sent_text

        # Nothing enqueued
        channel._coordinator.enqueue.assert_not_called()

    async def test_download_failure(self) -> None:
        """Download failure sends error message, does not enqueue."""
        channel = self._make_channel()
        channel._bot.download = AsyncMock(
            side_effect=TelegramAPIError(method="download", message="fail"),
        )
        channel._bot.send_message = AsyncMock(return_value=MockMessage())

        photo = MagicMock()
        photo.file_id = "photo_123"
        photo.width = 1280
        photo.height = 720
        photo.file_size = 250_000
        photo.file_name = None

        msg = self._make_media_message(photo=[photo])

        await channel._handle_media(msg)

        # Error message sent
        channel._bot.send_message.assert_called_once()
        sent_text = channel._bot.send_message.call_args[0][1]
        assert "Failed to download" in sent_text

        # Nothing enqueued
        channel._coordinator.enqueue.assert_not_called()

    async def test_unresolvable_media_ignored(self) -> None:
        """Message with no resolvable media is silently ignored."""
        channel = self._make_channel()
        msg = self._make_media_message()  # No media fields set

        await channel._handle_media(msg)

        channel._coordinator.enqueue.assert_not_called()


class TestDeliveryLock:
    """R2 / KD-5: entry points serialize via _delivery_lock."""

    def _make_channel(self) -> TelegramChannel:
        coordinator = _make_mock_coordinator()
        settings = MagicMock()
        settings.bot_token = "123456:ABCdef"
        settings.authorized_chat_id = 123
        settings.push_notifications = False

        with patch("tachikoma.telegram.Bot"):
            channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
            channel._TelegramChannel__coordinator = coordinator

        channel._bot = MagicMock()
        return channel

    async def test_handle_message_concurrent_second_call_steers(self) -> None:
        """Two concurrent _handle_message calls: first processes, second steers.

        Under the lock-as-gate semantics the lock is released only after the
        in-flight exchange ends, so any concurrent user message that arrives
        meanwhile takes the steering branch (enqueue-only) instead of starting
        a second exchange.
        """
        channel = self._make_channel()
        call_order: list[str] = []

        async def _fake_process(*args, **kw):
            call_order.append("enter")
            await asyncio.sleep(0)
            call_order.append("exit")

        channel._process_through_coordinator = AsyncMock(side_effect=_fake_process)

        msg1 = MagicMock(text="one")
        msg2 = MagicMock(text="two")

        await asyncio.gather(
            channel._handle_message(msg1),
            channel._handle_message(msg2),
        )

        # Only one exchange runs; the second call enqueued and returned.
        assert call_order == ["enter", "exit"]
        enqueued = [args[0][0].text for args in channel._coordinator.enqueue.call_args_list]
        assert sorted(enqueued) == ["one", "two"]

    async def test_handle_message_steers_when_lock_held(self) -> None:
        """User messages mid-exchange enqueue-only and skip the delivery lock.

        The lock-held check covers every phase of the in-flight exchange
        (boundary detection, pre-processing, SDK streaming, teardown), so a
        message arriving during pre-processing is also routed through the
        coordinator's forwarder onto the live sdk_inbox.
        """
        channel = self._make_channel()
        channel._process_through_coordinator = AsyncMock()

        msg = MagicMock(text="hey", message_id=99)

        await channel._delivery_lock.acquire()
        try:
            # With the lock held by another exchange, the steering branch
            # must enqueue immediately and return without blocking.
            await asyncio.wait_for(channel._handle_message(msg), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="hey", external_id="99")
        )
        channel._process_through_coordinator.assert_not_called()

    async def test_handle_media_steers_when_lock_held(self) -> None:
        """Media messages mid-exchange enqueue-only and skip the delivery lock."""
        channel = self._make_channel()
        channel._bot.download = AsyncMock(return_value=None)
        channel._process_through_coordinator = AsyncMock()

        photo = MagicMock()
        photo.file_id = "photo_steer"
        photo.width = 100
        photo.height = 100
        photo.file_size = 50_000
        photo.file_name = None

        msg = MagicMock()
        msg.photo = [photo]
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.sticker = None
        msg.video = None
        msg.video_note = None
        msg.animation = None
        msg.caption = None

        await channel._delivery_lock.acquire()
        try:
            await asyncio.wait_for(channel._handle_media(msg), timeout=0.5)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._process_through_coordinator.assert_not_called()

    async def test_handle_buffered_delivery_returns_immediately_when_lock_held(self) -> None:
        """_handle_buffered_delivery returns immediately even when the lock is held,
        spawning a detached task that queues on the lock."""
        channel = self._make_channel()
        channel._process_through_coordinator = AsyncMock()

        event = BufferedDelivery(prompt="digest", items=[], is_shutdown_digest=False)

        await channel._delivery_lock.acquire()
        try:
            # Handler should return immediately — it just spawns a task
            await asyncio.wait_for(channel._handle_buffered_delivery(event), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        # Wait for the spawned delivery task to complete
        tasks = list(channel._delivery_tasks)
        for task in tasks:
            await task

        channel._process_through_coordinator.assert_called_once()


class TestTelegramChannelBufferFlush:
    """R12: TelegramChannel flushes buffer in run() teardown."""

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_run_exit_calls_flush_on_shutdown(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """When run() exits, the buffer's flush_on_shutdown is awaited."""
        settings = MagicMock()
        settings.bot_token = "123:abc"
        settings.authorized_chat_id = 1

        buffer = MagicMock()
        buffer.flush_on_shutdown = AsyncMock()

        channel = TelegramChannel(
            settings,
            workspace_path=Path("/tmp/test-workspace"),
            buffer=buffer,
        )
        channel._dispatcher = MagicMock()
        channel._dispatcher.start_polling = AsyncMock()
        channel._dispatcher.stop_polling = AsyncMock()
        channel._dispatcher.include_router = MagicMock()
        channel._dispatcher.shutdown = MagicMock()
        channel._bot = MagicMock()
        channel._bot.set_my_commands = AsyncMock()

        mock_sys.stdin.isatty.return_value = False

        loop = MagicMock()
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

        buffer.flush_on_shutdown.assert_awaited_once()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_run_exit_without_buffer_skips_flush(
        self,
        mock_sys: MagicMock,
        mock_termios: MagicMock,
        mock_tty: MagicMock,
    ) -> None:
        """No buffer attached → run() exits cleanly without attempting flush."""
        settings = MagicMock()
        settings.bot_token = "123:abc"
        settings.authorized_chat_id = 1

        channel = TelegramChannel(
            settings,
            workspace_path=Path("/tmp/test-workspace"),
        )
        channel._dispatcher = MagicMock()
        channel._dispatcher.start_polling = AsyncMock()
        channel._dispatcher.stop_polling = AsyncMock()
        channel._dispatcher.include_router = MagicMock()
        channel._dispatcher.shutdown = MagicMock()
        channel._bot = MagicMock()
        channel._bot.set_my_commands = AsyncMock()

        mock_sys.stdin.isatty.return_value = False

        loop = MagicMock()
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            coordinator = _make_mock_coordinator()
            await channel.run(coordinator)

    async def test_second_signal_during_flush_force_exits(self) -> None:
        """KD-6/S15: second SIGINT/SIGTERM during flush cancels and force-exits."""
        settings = MagicMock()
        settings.bot_token = "123:abc"
        settings.authorized_chat_id = 1

        flush_should_complete = asyncio.Event()
        buffer = MagicMock()

        async def _slow_flush() -> None:
            await flush_should_complete.wait()

        buffer.flush_on_shutdown = _slow_flush

        coordinator = _make_mock_coordinator()
        coordinator.interrupt = AsyncMock()

        channel = TelegramChannel(
            settings,
            workspace_path=Path("/tmp/test-workspace"),
            buffer=buffer,
        )
        channel._TelegramChannel__coordinator = coordinator  # type: ignore[attr-defined]

        loop = asyncio.get_running_loop()
        captured: dict[str, object] = {}

        def fake_add(sig, callback, *args):  # noqa: ANN001, ANN202
            if sig == signal.SIGINT:
                captured["handler"] = callback
                captured["args"] = args

        # Patch only the relevant loop methods on the live loop
        original_add = loop.add_signal_handler
        original_remove = loop.remove_signal_handler
        loop.add_signal_handler = fake_add  # type: ignore[method-assign]
        loop.remove_signal_handler = lambda sig: True  # type: ignore[method-assign,assignment]

        try:
            flush_outer: asyncio.Task[bool] = asyncio.ensure_future(
                channel._flush_buffer_on_shutdown(loop)
            )
            await asyncio.sleep(0.01)

            assert "handler" in captured
            # Signal handler is added with the sig as the first arg
            captured["handler"](*captured["args"])  # type: ignore[operator,misc]

            force_exit = await flush_outer
            await asyncio.sleep(0)

            assert force_exit is True
            coordinator.interrupt.assert_awaited()
        finally:
            loop.add_signal_handler = original_add  # type: ignore[method-assign]
            loop.remove_signal_handler = original_remove  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Reply-to routing tests (DLT-086, Batch 3)
# ---------------------------------------------------------------------------


def _make_reply_channel(
    *,
    authorized_chat_id: int = 123,
    active_session_id: str = "session-current",
    lookup_result: str | None = None,
) -> tuple[TelegramChannel, MagicMock]:
    """Build a TelegramChannel with mocked registry for reply-to tests."""
    coordinator = _make_mock_coordinator()

    registry = AsyncMock()
    registry.get_active_session.return_value = MagicMock(id=active_session_id)
    registry.find_session_by_external_id.return_value = lookup_result
    coordinator._registry = registry

    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = authorized_chat_id
    settings.push_notifications = False

    with patch("tachikoma.telegram.Bot"):
        channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
        channel._TelegramChannel__coordinator = coordinator

    channel._bot = MagicMock()
    channel._process_through_coordinator = AsyncMock()
    channel._drain_deferred_queue = AsyncMock()
    return channel, registry


def _make_message(
    *,
    text: str = "hello",
    message_id: int = 100,
    reply_to_message_id: int | None = None,
) -> MagicMock:
    """Build a mock aiogram Message."""
    msg = MagicMock()
    msg.text = text
    msg.message_id = message_id
    msg.entities = None

    if reply_to_message_id is not None:
        reply = MagicMock()
        reply.message_id = reply_to_message_id
        msg.reply_to_message = reply
    else:
        msg.reply_to_message = None

    return msg


class TestExtractReplyTarget:
    """R1: _extract_reply_target extracts reply_to_message.message_id."""

    def test_returns_str_id_when_reply_to_set(self) -> None:
        channel, _ = _make_reply_channel()
        msg = _make_message(reply_to_message_id=42)
        assert channel._extract_reply_target(msg) == "42"

    def test_returns_none_when_no_reply(self) -> None:
        channel, _ = _make_reply_channel()
        msg = _make_message()
        assert channel._extract_reply_target(msg) is None


class TestResolveReplyTarget:
    """R3: _resolve_reply_target looks up session and compares to active."""

    async def test_returns_target_when_different_session(self) -> None:
        channel, registry = _make_reply_channel(
            active_session_id="session-current",
            lookup_result="session-past",
        )
        result = await channel._resolve_reply_target("42")
        assert result == "session-past"
        registry.find_session_by_external_id.assert_called_once_with("telegram", "42")

    async def test_returns_none_when_same_session(self) -> None:
        channel, registry = _make_reply_channel(
            active_session_id="session-current",
            lookup_result="session-current",
        )
        result = await channel._resolve_reply_target("42")
        assert result is None

    async def test_returns_none_when_lookup_returns_none(self) -> None:
        channel, registry = _make_reply_channel(lookup_result=None)
        result = await channel._resolve_reply_target("42")
        assert result is None

    async def test_returns_none_on_registry_error(self) -> None:
        channel, registry = _make_reply_channel()
        registry.find_session_by_external_id.side_effect = RuntimeError("db error")
        result = await channel._resolve_reply_target("42")
        assert result is None

    async def test_returns_none_when_no_registry(self) -> None:
        channel, _ = _make_reply_channel()
        channel._coordinator._registry = None
        result = await channel._resolve_reply_target("42")
        assert result is None

    async def test_returns_none_when_no_active_session(self) -> None:
        channel, registry = _make_reply_channel()
        registry.get_active_session.return_value = None
        result = await channel._resolve_reply_target("42")
        assert result is None


class TestReplyToRouting:
    """R4-R9: Reply-to routing in _handle_message and _handle_media."""

    async def test_reply_different_session_sets_target_session_id(self) -> None:
        """R4: Reply to different session sets target_session_id on envelope."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = _make_message(reply_to_message_id=42)

        await channel._handle_message(msg)

        channel._coordinator.enqueue_deferred.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert isinstance(envelope, TextMessage)
        assert envelope.target_session_id == "session-past"
        assert envelope.external_id == "100"

        registry.find_session_by_external_id.assert_called_once_with("telegram", "42")

    async def test_reply_same_session_routes_normally(self) -> None:
        """R5: Reply to current session routes normally, no target_session_id."""
        channel, registry = _make_reply_channel(
            active_session_id="session-current",
            lookup_result="session-current",
        )
        msg = _make_message(reply_to_message_id=42)

        await channel._handle_message(msg)

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_reply_unknown_message_routes_normally(self) -> None:
        """R6: Reply to unknown message routes through boundary detection."""
        channel, registry = _make_reply_channel(lookup_result=None)
        msg = _make_message(reply_to_message_id=999)

        await channel._handle_message(msg)

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_busy_reply_different_session_deferred(self) -> None:
        """R8: Busy + different session → enqueue_deferred with target_session_id."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = _make_message(reply_to_message_id=42)

        await channel._delivery_lock.acquire()
        try:
            await asyncio.wait_for(channel._handle_message(msg), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue_deferred.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert envelope.target_session_id == "session-past"

    async def test_busy_reply_same_session_steers(self) -> None:
        """R9: Busy + same session → enqueue (steering)."""
        channel, registry = _make_reply_channel(
            active_session_id="session-current",
            lookup_result="session-current",
        )
        msg = _make_message(reply_to_message_id=42)

        await channel._delivery_lock.acquire()
        try:
            await asyncio.wait_for(channel._handle_message(msg), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_busy_reply_unknown_session_steers(self) -> None:
        """R9: Busy + unknown session → enqueue (steering)."""
        channel, registry = _make_reply_channel(lookup_result=None)
        msg = _make_message(reply_to_message_id=999)

        await channel._delivery_lock.acquire()
        try:
            await asyncio.wait_for(channel._handle_message(msg), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_no_reply_no_target_session_id(self) -> None:
        """Non-reply messages have no target_session_id (unchanged behavior)."""
        channel, _ = _make_reply_channel()
        msg = _make_message()

        await channel._handle_message(msg)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None


class TestReplyToCommandPrecedence:
    """S5: Reply-to overrides /new and /queue commands."""

    async def test_reply_with_new_command_overrides_force_new(self) -> None:
        """Reply-to + /new → target_session_id set, force_new=False."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = _make_message(text="/new some text", reply_to_message_id=42)
        # Set up bot_command entity for /new detection
        entity = MagicMock()
        entity.type = "bot_command"
        entity.offset = 0
        entity.length = 4
        msg.entities = [entity]

        await channel._handle_message(msg)

        channel._coordinator.enqueue_deferred.assert_called_once()
        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert envelope.target_session_id == "session-past"
        assert envelope.force_new is False

    async def test_reply_with_queue_command_overrides(self) -> None:
        """Reply-to + /queue → target_session_id set, message deferred."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = _make_message(text="/queue some text", reply_to_message_id=42)
        entity = MagicMock()
        entity.type = "bot_command"
        entity.offset = 0
        entity.length = 6
        msg.entities = [entity]

        await channel._handle_message(msg)

        channel._coordinator.enqueue_deferred.assert_called_once()
        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert envelope.target_session_id == "session-past"

    async def test_new_command_without_reply_has_force_new(self) -> None:
        """/new without reply → force_new=True, no target_session_id (existing behavior)."""
        channel, _ = _make_reply_channel()
        msg = _make_message(text="/new some text")
        entity = MagicMock()
        entity.type = "bot_command"
        entity.offset = 0
        entity.length = 4
        msg.entities = [entity]

        await channel._handle_message(msg)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.force_new is True
        assert envelope.target_session_id is None


class TestReplyToMedia:
    """R2: Reply-to detection on media messages."""

    def _make_media_message(
        self,
        *,
        message_id: int = 100,
        reply_to_message_id: int | None = None,
    ) -> MagicMock:
        """Create a mock media message with optional reply_to_message."""
        photo = MagicMock()
        photo.file_id = "photo_123"
        photo.width = 800
        photo.height = 600
        photo.file_size = 100_000
        photo.file_name = None

        msg = MagicMock()
        msg.photo = [photo]
        msg.voice = None
        msg.audio = None
        msg.document = None
        msg.sticker = None
        msg.video = None
        msg.video_note = None
        msg.animation = None
        msg.caption = "A photo"
        msg.message_id = message_id

        if reply_to_message_id is not None:
            reply = MagicMock()
            reply.message_id = reply_to_message_id
            msg.reply_to_message = reply
        else:
            msg.reply_to_message = None

        return msg

    async def test_media_reply_sets_target_session_id(self) -> None:
        """R2: Media reply to different session sets target_session_id."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = self._make_media_message(reply_to_message_id=42)
        channel._bot.download = AsyncMock(return_value=None)

        await channel._handle_media(msg)

        channel._coordinator.enqueue_deferred.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert isinstance(envelope, TextMessage)
        assert envelope.target_session_id == "session-past"

    async def test_media_busy_reply_deferred(self) -> None:
        """R8: Busy media + different session → enqueue_deferred."""
        channel, registry = _make_reply_channel(
            lookup_result="session-past",
        )
        msg = self._make_media_message(reply_to_message_id=42)
        channel._bot.download = AsyncMock(return_value=None)

        await channel._delivery_lock.acquire()
        try:
            await asyncio.wait_for(channel._handle_media(msg), timeout=0.5)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue_deferred.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert envelope.target_session_id == "session-past"

    async def test_media_no_reply_no_target(self) -> None:
        """Media without reply has no target_session_id (existing behavior)."""
        channel, _ = _make_reply_channel()
        msg = self._make_media_message()
        channel._bot.download = AsyncMock(return_value=None)

        await channel._handle_media(msg)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_media_reply_unknown_routes_normally(self) -> None:
        """R6: Media reply to unknown message routes normally."""
        channel, registry = _make_reply_channel(lookup_result=None)
        msg = self._make_media_message(reply_to_message_id=999)
        channel._bot.download = AsyncMock(return_value=None)

        await channel._handle_media(msg)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None
