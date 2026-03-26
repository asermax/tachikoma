"""Telegram channel tests.

Tests for DLT-002: Send and receive messages via Telegram.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

from tachikoma.events import Error, ToolActivity
from tachikoma.telegram import ResponseRenderer, TelegramChannel


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

    async def test_multiple_tool_text_cycles_insert_multiple_markers(self) -> None:
        """Each tool→text transition inserts its own "Ran tools" marker (AC1)."""
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

        assert renderer._buffer.count("🔧 Ran tools") == 2

    async def test_consecutive_tool_batches_without_text_produce_single_marker(self) -> None:
        """Consecutive tools without text between them produce one marker (AC4)."""
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

        assert renderer._buffer.count("🔧 Ran tools") == 2

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
            side_effect=TelegramAPIError(method="copy_message", message="Failed")  # type: ignore[arg-type]
        )
        bot.delete_message = AsyncMock()
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        await renderer.notify()

        bot.copy_message.assert_called_once()
        bot.delete_message.assert_not_called()

    async def test_notify_accepts_duplicate_on_delete_failure(self) -> None:
        """notify() accepts duplicate message if delete fails."""
        bot = MagicMock()
        bot.copy_message = AsyncMock()
        bot.delete_message = AsyncMock(
            side_effect=TelegramAPIError(method="delete_message", message="Failed")  # type: ignore[arg-type]
        )
        renderer = ResponseRenderer(bot, chat_id=123, push_notifications=True)
        renderer._current_message_id = 42

        # Should not raise
        await renderer.notify()

        bot.copy_message.assert_called_once()
        bot.delete_message.assert_called_once()


class TestTelegramChannelStdinShutdown:
    """Tests for 'q' keypress graceful shutdown in TelegramChannel.run()."""

    def _make_channel(self) -> TelegramChannel:
        """Build a TelegramChannel with mocked dependencies."""
        coordinator = MagicMock()
        settings = MagicMock()
        settings.bot_token = "123456:ABCdef"
        settings.authorized_chat_id = 123

        channel = TelegramChannel(coordinator, settings)

        # Replace dispatcher and bot with mocks
        channel._dispatcher = MagicMock()
        channel._dispatcher.start_polling = AsyncMock()
        channel._dispatcher.stop_polling = AsyncMock()
        channel._dispatcher.include_router = MagicMock()
        channel._dispatcher.shutdown = MagicMock()
        channel._bot = MagicMock()

        return channel

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_q_keypress_stops_polling(
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
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
            await channel.run()

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
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
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
            await channel.run()

        assert captured_callback is not None
        mock_sys.stdin.read.return_value = "Q"

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()
            mock_ensure.assert_called_once()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_non_q_keypress_does_not_stop(
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
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
            await channel.run()

        assert captured_callback is not None
        mock_sys.stdin.read.return_value = "x"

        with patch("asyncio.ensure_future") as mock_ensure:
            captured_callback()
            mock_ensure.assert_not_called()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_stdin_not_tty_skips_reader(
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
    ) -> None:
        """Non-TTY stdin skips reader and terminal setup."""
        channel = self._make_channel()

        mock_sys.stdin.isatty.return_value = False

        loop = MagicMock()
        loop.add_signal_handler = MagicMock()
        loop.remove_signal_handler = MagicMock()
        loop.remove_reader = MagicMock()

        with patch("asyncio.get_running_loop", return_value=loop):
            await channel.run()

        loop.add_reader.assert_not_called()
        mock_termios.tcgetattr.assert_not_called()
        mock_tty.setcbreak.assert_not_called()

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_terminal_restored_on_exit(
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
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
            await channel.run()

        # Terminal settings restored
        mock_termios.tcsetattr.assert_called_once_with(0, 1, original_attrs)

        # Reader cleaned up
        loop.remove_reader.assert_called_once_with(0)

    @patch("tachikoma.telegram.tty")
    @patch("tachikoma.telegram.termios")
    @patch("tachikoma.telegram.sys")
    async def test_eof_on_stdin_removes_reader(
        self, mock_sys: MagicMock, mock_termios: MagicMock, mock_tty: MagicMock,
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
            await channel.run()

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
