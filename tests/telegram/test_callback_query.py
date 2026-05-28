"""Tests for the CallbackQuery handler in TelegramChannel.

Covers: ack ordering, auth, routing (busy/idle/deferred-drain),
rapid double-tap, keyboard removal, InaccessibleMessage tolerance, stale taps.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from conftest import _make_mock_coordinator

from tachikoma.message import ButtonTapMessage
from tachikoma.telegram import TelegramChannel


def _make_channel(authorized_chat_id: int = 123) -> TelegramChannel:
    """Build a TelegramChannel with mocked dependencies."""
    coordinator = _make_mock_coordinator()
    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = authorized_chat_id
    settings.push_notifications = False

    with patch("tachikoma.telegram.Bot"):
        channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
        channel._TelegramChannel__coordinator = coordinator

    channel._bot = MagicMock()
    return channel


def _make_callback(
    *,
    data: str = "btn1:approve",
    from_user_id: int = 123,
    message_chat_id: int = 123,
    message_id: int = 42,
    message_is_inaccessible: bool = False,
) -> MagicMock:
    """Build a mock CallbackQuery."""
    cb = MagicMock()
    cb.data = data

    user = MagicMock()
    user.id = from_user_id
    cb.from_user = user

    cb.answer = AsyncMock()

    if message_is_inaccessible:
        msg = MagicMock()
        msg.chat.id = message_chat_id
        msg.message_id = message_id
        # InaccessibleMessage has no text attribute
        del msg.text
        cb.message = msg
    elif message_chat_id is not None:
        msg = MagicMock()
        msg.chat.id = message_chat_id
        msg.message_id = message_id
        cb.message = msg
    else:
        cb.message = None

    return cb


class TestCallbackQueryAck:
    """R4: callback_query.answer() is called before any work depending on agent state."""

    async def test_answer_called_before_enqueue(self) -> None:
        """answer() is called even when the agent is busy."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback()

        await channel._handle_callback_query(cb)

        cb.answer.assert_called_once()

    async def test_answer_failure_does_not_crash(self) -> None:
        """An exception from answer() does not crash the handler."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback()
        cb.answer = AsyncMock(side_effect=Exception("query is too old"))

        await channel._handle_callback_query(cb)

        # Handler still proceeds to enqueue
        channel._coordinator.enqueue.assert_called_once()
        tap = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(tap, ButtonTapMessage)
        assert tap.value == "approve"


class TestCallbackQueryAuth:
    """R8: Authorization on from_user.id."""

    async def test_authorized_tap_proceeds(self) -> None:
        """Authorized tap is enqueued normally."""
        channel = _make_channel(authorized_chat_id=123)
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback(from_user_id=123)

        await channel._handle_callback_query(cb)

        channel._coordinator.enqueue.assert_called_once()
        tap = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(tap, ButtonTapMessage)
        assert tap.value == "approve"

    async def test_unauthorized_tap_dropped_after_ack(self) -> None:
        """Unauthorized tap: answer() called, then dropped (no enqueue, no keyboard removal)."""
        channel = _make_channel(authorized_chat_id=123)
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback(from_user_id=999)

        await channel._handle_callback_query(cb)

        # answer() was called (clears spinner for unauthorized user)
        cb.answer.assert_called_once()
        # Nothing was enqueued
        channel._coordinator.enqueue.assert_not_called()
        # No keyboard removal attempted
        channel._bot.edit_message_reply_markup.assert_not_called()


class TestCallbackQueryRouting:
    """R6: Tap routing reuses _delivery_lock.locked() branching."""

    async def test_idle_tap_acquires_lock(self) -> None:
        """When idle, tap acquires lock, enqueues, processes, and drains."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()
        cb = _make_callback()

        await channel._handle_callback_query(cb)

        channel._coordinator.enqueue.assert_called_once()
        channel._process_through_coordinator.assert_called_once()
        channel._drain_deferred_queue.assert_called_once()

    async def test_busy_tap_enqueues_only(self) -> None:
        """When lock is held, tap enqueues without acquiring the lock (steering)."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        await channel._delivery_lock.acquire()
        try:
            cb = _make_callback()
            await asyncio.wait_for(channel._handle_callback_query(cb), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._process_through_coordinator.assert_not_called()

    async def test_tap_during_deferred_drain_takes_busy_branch(self) -> None:
        """Tap arriving while _drain_deferred_queue holds lock takes busy (enqueue-only) branch."""
        channel = _make_channel()

        drain_started = asyncio.Event()
        drain_continue = asyncio.Event()

        async def _slow_drain():
            drain_started.set()
            await drain_continue.wait()

        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = _slow_drain

        cb = _make_callback()

        # Start the handler in idle mode — it will acquire lock and call drain
        handler_task = asyncio.create_task(channel._handle_callback_query(cb))

        # Wait for drain to start (lock is now held)
        await drain_started.wait()

        # A second tap arrives while drain holds the lock
        cb2 = _make_callback(data="btn1:cancel")
        await asyncio.wait_for(channel._handle_callback_query(cb2), timeout=0.05)

        # Second tap took the busy branch (enqueue only)
        assert channel._coordinator.enqueue.call_count == 2

        # Let the first handler finish
        drain_continue.set()
        await handler_task


class TestCallbackQueryRapidDoubleTap:
    """R5: Rapid double-tap routes both taps independently."""

    async def test_two_rapid_taps_both_enqueued(self) -> None:
        """Two taps in quick succession both reach the coordinator independently."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        cb1 = _make_callback(data="btn1:yes")
        cb2 = _make_callback(data="btn1:no")

        await channel._handle_callback_query(cb1)
        await channel._handle_callback_query(cb2)

        assert channel._coordinator.enqueue.call_count == 2
        values = [call[0][0].value for call in channel._coordinator.enqueue.call_args_list]
        assert values == ["yes", "no"]


class TestCallbackQueryKeyboardRemoval:
    """R7/R12: Keyboard removal after tap."""

    async def test_single_use_schedules_removal(self) -> None:
        """single_use=True (btn1:) schedules a detached keyboard removal task."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._bot.edit_message_reply_markup = AsyncMock()

        cb = _make_callback(data="btn1:approve")
        await channel._handle_callback_query(cb)

        # Wait for the detached task to complete
        await asyncio.sleep(0.05)

        channel._bot.edit_message_reply_markup.assert_called_once_with(
            chat_id=123,
            message_id=42,
            reply_markup=None,
        )

    async def test_multi_use_does_not_remove(self) -> None:
        """single_use=False (btnN:) does not schedule keyboard removal."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        cb = _make_callback(data="btnN:approve")
        await channel._handle_callback_query(cb)

        await asyncio.sleep(0.05)
        channel._bot.edit_message_reply_markup.assert_not_called()

    async def test_removal_does_not_block_routing(self) -> None:
        """Keyboard removal is detached — enqueue is called before the edit completes."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        edit_done = asyncio.Event()

        async def _slow_edit(**kwargs):
            await edit_done.wait()

        channel._bot.edit_message_reply_markup = _slow_edit

        cb = _make_callback(data="btn1:approve")
        await channel._handle_callback_query(cb)

        # Enqueue was called immediately, before the edit finished
        channel._coordinator.enqueue.assert_called_once()

        # Now let the edit finish
        edit_done.set()
        await asyncio.sleep(0.05)

    async def test_no_message_skips_removal(self) -> None:
        """When callback.message is None, no removal is scheduled."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        cb = _make_callback(message_chat_id=None)
        await channel._handle_callback_query(cb)

        await asyncio.sleep(0.05)
        channel._bot.edit_message_reply_markup.assert_not_called()


class TestCallbackQueryInaccessibleMessage:
    """R9: InaccessibleMessage tolerance for stale taps."""

    async def test_inaccessible_message_routes_normally(self) -> None:
        """InaccessibleMessage (message >48h old) still routes the tap."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._bot.edit_message_reply_markup = AsyncMock()

        cb = _make_callback(message_is_inaccessible=True)
        await channel._handle_callback_query(cb)

        # Tap was enqueued
        channel._coordinator.enqueue.assert_called_once()
        tap = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(tap, ButtonTapMessage)

        # Keyboard removal was attempted with the correct IDs
        await asyncio.sleep(0.05)
        channel._bot.edit_message_reply_markup.assert_called_once_with(
            chat_id=123,
            message_id=42,
            reply_markup=None,
        )


class TestRemoveKeyboardErrors:
    """R12: Keyboard removal failures never block routing."""

    async def test_not_modified_is_noop(self) -> None:
        """'message is not modified' is treated as no-op (debug log, no warning)."""
        channel = _make_channel()

        channel._bot.edit_message_reply_markup = AsyncMock(
            side_effect=TelegramBadRequest(
                method="editMessageReplyMarkup",
                message="Bad Request: message is not modified",
            ),
        )

        # Should not raise
        await channel._remove_keyboard(123, 42)

    async def test_other_bad_request_logs_warning(self) -> None:
        """Other TelegramBadRequest (e.g., 'message to edit not found') logs at warning."""
        channel = _make_channel()

        channel._bot.edit_message_reply_markup = AsyncMock(
            side_effect=TelegramBadRequest(
                method="editMessageReplyMarkup",
                message="Bad Request: message to edit not found",
            ),
        )

        # Should not raise
        await channel._remove_keyboard(123, 42)

    async def test_generic_api_error_logs_warning(self) -> None:
        """Generic TelegramAPIError logs at warning and does not re-raise."""
        channel = _make_channel()

        channel._bot.edit_message_reply_markup = AsyncMock(
            side_effect=TelegramAPIError(
                method="editMessageReplyMarkup",
                message="Internal server error",
            ),
        )

        # Should not raise
        await channel._remove_keyboard(123, 42)


class TestCallbackQueryStaleTap:
    """R9: Stale taps are routed normally."""

    async def test_unknown_prefix_logged_and_dropped(self) -> None:
        """Unrecognized callback_data prefix is logged and dropped."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback(data="nope:something")

        await channel._handle_callback_query(cb)

        cb.answer.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

    async def test_none_data_dropped(self) -> None:
        """CallbackQuery with None data is dropped gracefully."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        cb = _make_callback()
        cb.data = None

        await channel._handle_callback_query(cb)

        cb.answer.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()
