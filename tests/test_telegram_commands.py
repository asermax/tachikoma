"""Tests for Telegram command detection, routing, and deferred queue drain."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import MessageEntity
from conftest import _make_mock_coordinator

from tachikoma.message import TextMessage
from tachikoma.telegram import TelegramChannel


def _make_message(
    text: str,
    entities: list[MessageEntity] | None = None,
    message_id: int = 42,
) -> MagicMock:
    """Build a mock aiogram Message with the given text and entities."""
    msg = MagicMock()
    msg.text = text
    msg.entities = entities
    msg.message_id = message_id
    return msg


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

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="hello", external_id="42")
        )
        ch._coordinator.enqueue_deferred.assert_not_called()

    async def test_busy_normal_message_enqueues_steering(self) -> None:
        """Normal text when busy is steered via regular enqueue."""
        ch = _make_telegram_channel()
        # Acquire the lock to simulate busy state
        await ch._delivery_lock.acquire()
        msg = _make_message("hello", None)

        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="hello", external_id="42")
        )
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

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="/new", external_id="42")
        )

    async def test_bare_new_when_busy_treated_as_normal(self) -> None:
        """/new (bare) when busy is steered as a normal message."""
        ch = _make_telegram_channel()
        await ch._delivery_lock.acquire()

        msg = _make_message("/new", [_make_entity("/new")])
        await ch._handle_message(msg)

        ch._coordinator.enqueue.assert_called_once_with(
            TextMessage(text="/new", external_id="42")
        )
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
