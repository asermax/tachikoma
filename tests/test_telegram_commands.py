"""Tests for Telegram command detection, routing, and deferred queue drain."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import MessageEntity
from conftest import _make_mock_coordinator, _make_mock_message

from tachikoma.message import TextMessage
from tachikoma.telegram import TelegramChannel, _truncate_reply_text


def _make_message(text, entities=None, message_id=42):
    return _make_mock_message(text=text, entities=entities, message_id=message_id)


def _make_entity(cmd: str, offset: int = 0) -> MessageEntity:
    """Build a bot_command MessageEntity for the given command string."""
    return MessageEntity(type="bot_command", offset=offset, length=len(cmd))


def _make_telegram_channel() -> TelegramChannel:
    """Create a TelegramChannel-like object with mocked coordinator."""
    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = 123
    settings.push_notifications = False

    channel = TelegramChannel(settings, workspace_path=Path("/tmp/test"))
    channel._bot = MagicMock()
    channel._bot.set_my_commands = AsyncMock()
    coordinator = _make_mock_coordinator()
    coordinator.enqueue = MagicMock()
    coordinator.enqueue_deferred = MagicMock()
    coordinator.promote_next_deferred = MagicMock()
    channel._TelegramChannel__coordinator = coordinator
    return channel


class TestDetectCommand:
    """Tests for TelegramChannel._detect_command()."""

    def test_new_with_args(self) -> None:
        """/new hello returns ("new", "hello")."""
        ch = _make_telegram_channel()
        msg = _make_message("/new hello", [_make_entity("/new")])
        assert ch._detect_command(msg) == ("new", "hello")

    def test_queue_with_args(self) -> None:
        """/queue remind me returns ("queue", "remind me")."""
        ch = _make_telegram_channel()
        msg = _make_message("/queue remind me", [_make_entity("/queue")])
        assert ch._detect_command(msg) == ("queue", "remind me")

    def test_bare_new_returns_none(self) -> None:
        """/new alone (no args) returns (None, "/new")."""
        ch = _make_telegram_channel()
        msg = _make_message("/new", [_make_entity("/new")])
        assert ch._detect_command(msg) == (None, "/new")

    def test_whitespace_only_args_returns_none(self) -> None:
        """/new   (whitespace-only) returns (None, full text)."""
        ch = _make_telegram_channel()
        msg = _make_message("/new   ", [_make_entity("/new")])
        assert ch._detect_command(msg) == (None, "/new   ")

    def test_nested_commands_first_wins(self) -> None:
        """/new /queue msg returns ("new", "/queue msg")."""
        ch = _make_telegram_channel()
        msg = _make_message("/new /queue msg", [_make_entity("/new")])
        assert ch._detect_command(msg) == ("new", "/queue msg")

    def test_plain_text_no_entities(self) -> None:
        """Plain text with no entities returns (None, text)."""
        ch = _make_telegram_channel()
        msg = _make_message("hello world", None)
        assert ch._detect_command(msg) == (None, "hello world")

    def test_plain_text_empty_entities(self) -> None:
        """Plain text with empty entity list returns (None, text)."""
        ch = _make_telegram_channel()
        msg = _make_message("hello world", [])
        assert ch._detect_command(msg) == (None, "hello world")

    def test_unrecognized_command(self) -> None:
        """/start (unrecognized) returns (None, text)."""
        ch = _make_telegram_channel()
        msg = _make_message("/start", [_make_entity("/start")])
        assert ch._detect_command(msg) == (None, "/start")

    def test_botname_suffix_stripped(self) -> None:
        """/new@mybot hello strips @mybot and returns ("new", "hello")."""
        ch = _make_telegram_channel()
        msg = _make_message("/new@mybot hello", [_make_entity("/new@mybot")])
        assert ch._detect_command(msg) == ("new", "hello")

    def test_entity_not_at_offset_zero_ignored(self) -> None:
        """A bot_command entity not at offset 0 is ignored."""
        ch = _make_telegram_channel()
        entity = MessageEntity(type="bot_command", offset=5, length=4)
        msg = _make_message("test /new hello", [entity])
        assert ch._detect_command(msg) == (None, "test /new hello")

    def test_queue_bare_returns_none(self) -> None:
        """/queue alone returns (None, "/queue")."""
        ch = _make_telegram_channel()
        msg = _make_message("/queue", [_make_entity("/queue")])
        assert ch._detect_command(msg) == (None, "/queue")

    def test_new_with_long_message(self) -> None:
        """/new with multi-word args extracts full args."""
        ch = _make_telegram_channel()
        msg = _make_message("/new let's talk about X", [_make_entity("/new")])
        assert ch._detect_command(msg) == ("new", "let's talk about X")


class TestHandleMessageRouting:
    """Tests for _handle_message routing with command detection."""

    async def test_idle_normal_message_enqueues(self) -> None:
        """Normal text when idle is enqueued via regular enqueue."""
        ch = _make_telegram_channel()
        msg = _make_message("hello", None)

        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(TextMessage(text="hello", external_id="42"))
        ch._coordinator.enqueue_deferred.assert_not_called()

    async def test_busy_normal_message_enqueues_steering(self) -> None:
        """Normal text when busy is steered via regular enqueue."""
        ch = _make_telegram_channel()
        # Acquire the lock to simulate busy state
        await ch._delivery_lock.acquire()
        msg = _make_message("hello", None)

        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(TextMessage(text="hello", external_id="42"))
        ch._coordinator.enqueue_deferred.assert_not_called()
        ch._delivery_lock.release()

    async def test_idle_new_command_enqueues_with_force_new(self) -> None:
        """/new with args when idle enqueues with force_new=True."""
        ch = _make_telegram_channel()
        # Mock _process_through_coordinator to avoid actually running it
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_message("/new fresh start", [_make_entity("/new")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="fresh start", force_new=True, external_id="42")
        )
        ch._coordinator.enqueue_deferred.assert_not_called()

    async def test_idle_queue_command_enqueues_normally(self) -> None:
        """/queue with args when idle enqueues without force_new."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_message("/queue something", [_make_entity("/queue")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="something", external_id="42")
        )
        ch._coordinator.enqueue_deferred.assert_not_called()

    async def test_busy_new_command_deferred(self) -> None:
        """/new with args when busy is deferred with force_new=True."""
        ch = _make_telegram_channel()
        await ch._delivery_lock.acquire()

        msg = _make_message("/new fresh start", [_make_entity("/new")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue_deferred.assert_called_once_with(
            TextMessage(text="fresh start", force_new=True, external_id="42")
        )
        ch._coordinator.enqueue.assert_not_called()
        ch._delivery_lock.release()

    async def test_busy_queue_command_deferred(self) -> None:
        """/queue with args when busy is deferred without force_new."""
        ch = _make_telegram_channel()
        await ch._delivery_lock.acquire()

        msg = _make_message("/queue remind me", [_make_entity("/queue")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue_deferred.assert_called_once_with(
            TextMessage(text="remind me", external_id="42")
        )
        ch._coordinator.enqueue.assert_not_called()
        ch._delivery_lock.release()

    async def test_bare_new_when_idle_treated_as_normal(self) -> None:
        """/new (bare) when idle is treated as a normal message."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_message("/new", [_make_entity("/new")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(TextMessage(text="/new", external_id="42"))

    async def test_bare_new_when_busy_treated_as_normal(self) -> None:
        """/new (bare) when busy is steered as a normal message."""
        ch = _make_telegram_channel()
        await ch._delivery_lock.acquire()

        msg = _make_message("/new", [_make_entity("/new")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(TextMessage(text="/new", external_id="42"))
        ch._coordinator.enqueue_deferred.assert_not_called()
        ch._delivery_lock.release()

    async def test_idle_triggers_drain_after_processing(self) -> None:
        """After idle delivery, _drain_deferred_queue is called."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_message("hello", None)
        await ch._handle_message(msg)

        ch._drain_deferred_queue.assert_called_once()

    async def test_empty_message_ignored(self) -> None:
        """Empty messages are ignored."""
        ch = _make_telegram_channel()
        msg = _make_message("", None)
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_not_called()
        ch._coordinator.enqueue_deferred.assert_not_called()

    async def test_whitespace_message_ignored(self) -> None:
        """Whitespace-only messages are ignored."""
        ch = _make_telegram_channel()
        msg = _make_message("   ", None)
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_not_called()
        ch._coordinator.enqueue_deferred.assert_not_called()


class TestDrainDeferredQueue:
    """Tests for _drain_deferred_queue."""

    async def test_empty_queue_is_noop(self) -> None:
        """Draining an empty queue does nothing."""
        ch = _make_telegram_channel()
        ch._coordinator.has_deferred = False
        ch._process_through_coordinator = AsyncMock()

        await ch._drain_deferred_queue()

        ch._coordinator.promote_next_deferred.assert_not_called()
        ch._process_through_coordinator.assert_not_called()

    async def test_drains_single_item(self) -> None:
        """A single deferred item is promoted and processed."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()

        remaining = 1

        def _promote() -> None:
            nonlocal remaining
            remaining -= 1
            ch._coordinator.has_deferred = remaining > 0

        ch._coordinator.has_deferred = True
        ch._coordinator.promote_next_deferred = MagicMock(side_effect=_promote)

        await ch._drain_deferred_queue()

        ch._coordinator.promote_next_deferred.assert_called_once()
        ch._process_through_coordinator.assert_called_once()

    async def test_drains_multiple_items_fifo(self) -> None:
        """Multiple deferred items are drained in FIFO order."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()

        remaining = 3

        def _promote() -> None:
            nonlocal remaining
            remaining -= 1
            ch._coordinator.has_deferred = remaining > 0

        ch._coordinator.has_deferred = True
        ch._coordinator.promote_next_deferred = MagicMock(side_effect=_promote)

        await ch._drain_deferred_queue()

        assert ch._coordinator.promote_next_deferred.call_count == 3
        assert ch._process_through_coordinator.call_count == 3

    async def test_error_in_one_item_does_not_block_rest(self) -> None:
        """An error processing one item does not prevent draining the rest."""
        ch = _make_telegram_channel()

        remaining = 2
        call_count = 0

        async def _process() -> None:
            nonlocal call_count, remaining
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            remaining -= 1
            ch._coordinator.has_deferred = remaining > 0

        def _promote() -> None:
            nonlocal remaining
            remaining -= 1
            ch._coordinator.has_deferred = remaining > 0

        ch._coordinator.has_deferred = True
        ch._coordinator.promote_next_deferred = MagicMock(side_effect=_promote)
        ch._process_through_coordinator = _process

        await ch._drain_deferred_queue()

        assert ch._coordinator.promote_next_deferred.call_count == 2


class TestTruncateReplyText:
    """Tests for _truncate_reply_text()."""

    def test_short_text_unchanged(self) -> None:
        """Text <= 200 chars is returned as-is."""
        text = "short message"
        assert _truncate_reply_text(text) == text

    def test_exactly_200_chars_unchanged(self) -> None:
        """Text exactly 200 chars is returned as-is."""
        text = "a" * 200
        assert _truncate_reply_text(text) == text

    def test_long_text_truncated(self) -> None:
        """Text > 200 chars is truncated with head + [...] + tail."""
        text = "a" * 100 + "MIDDLE" + "b" * 100
        result = _truncate_reply_text(text)
        assert result == "a" * 100 + "[...]" + "b" * 100

    def test_exactly_201_chars_truncated(self) -> None:
        """Text of exactly 201 chars is truncated."""
        text = "x" * 201
        result = _truncate_reply_text(text)
        assert "[...]" in result
        assert result.startswith("x" * 100)
        assert result.endswith("x" * 100)


class TestBuildReplyContext:
    """Tests for TelegramChannel._build_reply_context()."""

    def test_short_reply_text(self) -> None:
        """Short replied-to text is returned as a formatted prefix."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text="original message",
        )
        assert ch._build_reply_context(msg) == "Replied to:\n> original message"

    def test_long_reply_text_truncated(self) -> None:
        """Long replied-to text is truncated in the prefix."""
        ch = _make_telegram_channel()
        original = "a" * 100 + "MIDDLE" + "b" * 100
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text=original,
        )
        result = ch._build_reply_context(msg)
        assert result == "Replied to:\n> " + "a" * 100 + "[...]" + "b" * 100

    def test_caption_used_when_no_text(self) -> None:
        """Caption is used as fallback when text is None."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text=None,
            reply_to_caption="photo caption",
        )
        assert ch._build_reply_context(msg) == "Replied to:\n> photo caption"

    def test_long_caption_truncated(self) -> None:
        """Long caption is truncated in the prefix."""
        ch = _make_telegram_channel()
        caption = "a" * 100 + "MIDDLE" + "b" * 100
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text=None,
            reply_to_caption=caption,
        )
        result = ch._build_reply_context(msg)
        assert result == "Replied to:\n> " + "a" * 100 + "[...]" + "b" * 100

    def test_no_text_no_caption_returns_none(self) -> None:
        """Sticker (no text/caption) returns None."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text=None,
            reply_to_caption=None,
        )
        assert ch._build_reply_context(msg) is None

    def test_whitespace_only_text_returns_none(self) -> None:
        """Whitespace-only replied-to text returns None."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text="   ",
        )
        assert ch._build_reply_context(msg) is None

    def test_non_reply_returns_none(self) -> None:
        """Non-reply message returns None."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(text="hello")
        assert ch._build_reply_context(msg) is None

    def test_text_stripped(self) -> None:
        """Replied-to text is stripped of leading/trailing whitespace."""
        ch = _make_telegram_channel()
        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text="  hello world  ",
        )
        assert ch._build_reply_context(msg) == "Replied to:\n> hello world"


class TestReplyContextIntegration:
    """Integration tests verifying message_prefix on TextMessage envelopes."""

    async def test_reply_sets_message_prefix(self) -> None:
        """Replying to a message sets message_prefix on the envelope."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_mock_message(
            text="my reply",
            reply_to_message_id=99,
            reply_to_text="original message",
        )
        await ch._handle_message(msg)

        call_args = ch._coordinator.enqueue.call_args[0][0]
        assert isinstance(call_args, TextMessage)
        assert call_args.text == "my reply"
        assert call_args.message_prefix == "Replied to:\n> original message"

    async def test_non_reply_no_message_prefix(self) -> None:
        """Non-reply message has no message_prefix."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_mock_message(text="hello")
        await ch._handle_message(msg)

        call_args = ch._coordinator.enqueue.call_args[0][0]
        assert isinstance(call_args, TextMessage)
        assert call_args.message_prefix is None

    async def test_reply_to_sticker_no_message_prefix(self) -> None:
        """Reply to a sticker (no text) has no message_prefix."""
        ch = _make_telegram_channel()
        ch._process_through_coordinator = AsyncMock()
        ch._drain_deferred_queue = AsyncMock()

        msg = _make_mock_message(
            text="funny sticker",
            reply_to_message_id=99,
            reply_to_text=None,
            reply_to_caption=None,
        )
        await ch._handle_message(msg)

        call_args = ch._coordinator.enqueue.call_args[0][0]
        assert isinstance(call_args, TextMessage)
        assert call_args.message_prefix is None


class TestTextMessageSdkInput:
    """Tests for TextMessage.sdk_input with message_prefix."""

    def test_with_message_prefix(self) -> None:
        """sdk_input includes prefix when message_prefix is set."""
        msg = TextMessage(text="my reply", message_prefix="Replied to:\n> original")
        assert msg.sdk_input == "Replied to:\n> original\n\nmy reply"

    def test_without_message_prefix(self) -> None:
        """sdk_input returns plain text when message_prefix is None."""
        msg = TextMessage(text="hello")
        assert msg.sdk_input == "hello"
