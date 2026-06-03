"""Tests for the message_reaction handler in TelegramChannel.

Covers: emoji filtering, diff computation, routing (busy/idle),
auth filter, empty-diff early return, inbound_reactions config gating.
"""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import ReactionTypeCustomEmoji, ReactionTypeEmoji, ReactionTypePaid
from conftest import _make_channel_with_registry, _make_mock_coordinator

from tachikoma.message import ReactionMessage
from tachikoma.sessions.model import Session
from tachikoma.telegram import TelegramChannel, _emoji_set


def _make_channel(
    authorized_chat_id: int = 123,
    inbound_reactions: bool = True,
) -> TelegramChannel:
    """Build a TelegramChannel with mocked dependencies."""
    coordinator = _make_mock_coordinator()
    settings = MagicMock()
    settings.bot_token = "123456:ABCdef"
    settings.authorized_chat_id = authorized_chat_id
    settings.push_notifications = False
    settings.inbound_reactions = inbound_reactions

    with patch("tachikoma.telegram.Bot"):
        channel = TelegramChannel(settings, workspace_path=Path("/tmp/test-workspace"))
        channel._TelegramChannel__coordinator = coordinator

    channel._bot = MagicMock()
    return channel


def _make_emoji(emoji: str) -> MagicMock:
    """Build a mock ReactionTypeEmoji."""
    r = MagicMock(spec=ReactionTypeEmoji)
    r.emoji = emoji
    return r


def _make_paid() -> MagicMock:
    """Build a mock ReactionTypePaid."""
    return MagicMock(spec=ReactionTypePaid)


def _make_custom() -> MagicMock:
    """Build a mock ReactionTypeCustomEmoji."""
    r = MagicMock(spec=ReactionTypeCustomEmoji)
    r.custom_emoji_id = "abc123"
    return r


def _make_reaction_event(
    *,
    chat_id: int = 123,
    user_id: int = 123,
    message_id: int = 42,
    old_reaction: list | None = None,
    new_reaction: list | None = None,
) -> MagicMock:
    """Build a mock MessageReactionUpdated."""
    event = MagicMock()

    chat = MagicMock()
    chat.id = chat_id
    event.chat = chat

    user = MagicMock()
    user.id = user_id
    event.user = user

    event.message_id = message_id
    event.old_reaction = old_reaction or []
    event.new_reaction = new_reaction or []

    return event


class TestEmojiSet:
    """_emoji_set filters emoji-only entries."""

    def test_filters_emoji_only(self) -> None:
        reactions = [_make_emoji("👍"), _make_emoji("❤")]
        assert _emoji_set(reactions) == frozenset({"👍", "❤"})

    def test_ignores_paid(self) -> None:
        reactions = [_make_emoji("👍"), _make_paid()]
        assert _emoji_set(reactions) == frozenset({"👍"})

    def test_ignores_custom(self) -> None:
        reactions = [_make_emoji("👍"), _make_custom()]
        assert _emoji_set(reactions) == frozenset({"👍"})

    def test_empty_list(self) -> None:
        assert _emoji_set([]) == frozenset()

    def test_custom_only_returns_empty(self) -> None:
        assert _emoji_set([_make_custom()]) == frozenset()

    def test_paid_only_returns_empty(self) -> None:
        assert _emoji_set([_make_paid()]) == frozenset()

    def test_mixed_three_kinds_returns_emoji_only(self) -> None:
        reactions = [_make_emoji("👍"), _make_custom(), _make_paid()]
        assert _emoji_set(reactions) == frozenset({"👍"})


class TestReactionDiff:
    """Handler computes added/removed diff correctly."""

    async def test_added_only(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[],
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset({"👍"})
        assert envelope.removed == frozenset()

    async def test_removed_only(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_emoji("👍")],
            new_reaction=[],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset()
        assert envelope.removed == frozenset({"👍"})

    async def test_replacement(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_emoji("👍")],
            new_reaction=[_make_emoji("❤")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset({"❤"})
        assert envelope.removed == frozenset({"👍"})

    async def test_non_emoji_reactions_ignored(self) -> None:
        """Custom/paid reactions are filtered out of the diff."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_paid()],
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset({"👍"})
        assert envelope.removed == frozenset()


class TestReactionEmptyDiff:
    """Handler returns early when diff is empty."""

    async def test_no_emoji_change_no_enqueue(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_emoji("👍")],
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_not_called()

    async def test_only_non_emoji_reactions_no_enqueue(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_paid()],
            new_reaction=[_make_paid()],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_not_called()


class TestReactionRouting:
    """R6: Reaction routing reuses _delivery_lock.locked() branching."""

    async def test_idle_reaction_acquires_lock(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(new_reaction=[_make_emoji("👍")])
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        channel._process_through_coordinator.assert_called_once()
        channel._drain_deferred_queue.assert_called_once()

    async def test_busy_reaction_enqueues_only(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()

        await channel._delivery_lock.acquire()
        try:
            event = _make_reaction_event(new_reaction=[_make_emoji("👍")])
            await asyncio.wait_for(channel._handle_reaction(event), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._process_through_coordinator.assert_not_called()


class TestReactionMultiChange:
    """Handler computes multi-emoji diffs correctly."""

    async def test_multi_change_diff(self) -> None:
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[_make_emoji("🤔")],
            new_reaction=[_make_emoji("👍"), _make_emoji("❤")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset({"👍", "❤"})
        assert envelope.removed == frozenset({"🤔"})

    async def test_mixed_standard_and_custom_produces_standard_only(self) -> None:
        """Mixed update (standard + custom added) → envelope only has standard emoji."""
        channel = _make_channel()
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            old_reaction=[],
            new_reaction=[_make_emoji("👍"), _make_custom()],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.added == frozenset({"👍"})
        assert envelope.removed == frozenset()


class TestReactionHandlerRegistration:
    """Handler registration is gated by settings.inbound_reactions (KD-8)."""

    def test_handler_registered_when_inbound_reactions_true(self) -> None:
        channel = _make_channel(inbound_reactions=True)
        update_types = channel._dispatcher.resolve_used_update_types()

        assert "message_reaction" in update_types

    def test_handler_not_registered_when_inbound_reactions_false(self) -> None:
        channel = _make_channel(inbound_reactions=False)
        update_types = channel._dispatcher.resolve_used_update_types()

        assert "message_reaction" not in update_types


class TestReactionAllowedUpdates:
    """start_polling receives allowed_updates from resolve_used_update_types (KD-5)."""

    async def test_run_passes_resolved_update_types_with_reactions(self) -> None:
        """When inbound_reactions=True, start_polling gets message_reaction in allowed_updates."""
        channel = _make_channel(inbound_reactions=True)
        channel._bot.set_my_commands = AsyncMock()
        coordinator = _make_mock_coordinator()

        with patch.object(
            channel._dispatcher, "start_polling", new_callable=AsyncMock
        ) as mock_poll:
            mock_poll.side_effect = KeyboardInterrupt
            with contextlib.suppress(KeyboardInterrupt):
                await channel.run(coordinator)

        kwargs = mock_poll.call_args.kwargs
        assert kwargs["allowed_updates"] == channel._dispatcher.resolve_used_update_types()
        assert "message_reaction" in kwargs["allowed_updates"]

    async def test_run_passes_resolved_update_types_without_reactions(self) -> None:
        """When inbound_reactions=False, start_polling does NOT get message_reaction."""
        channel = _make_channel(inbound_reactions=False)
        channel._bot.set_my_commands = AsyncMock()
        coordinator = _make_mock_coordinator()

        with patch.object(
            channel._dispatcher, "start_polling", new_callable=AsyncMock
        ) as mock_poll:
            mock_poll.side_effect = KeyboardInterrupt
            with contextlib.suppress(KeyboardInterrupt):
                await channel.run(coordinator)

        kwargs = mock_poll.call_args.kwargs
        assert "message_reaction" not in kwargs["allowed_updates"]


def _make_reaction_channel(
    *,
    authorized_chat_id: int = 123,
    active_session_id: str = "session-current",
    lookup_result: str | None = None,
    last_exchange: str | None = None,
) -> tuple[TelegramChannel, MagicMock]:
    """Build a TelegramChannel with a mocked registry on the coordinator."""
    channel, registry = _make_channel_with_registry(
        authorized_chat_id=authorized_chat_id,
        active_session_id=active_session_id,
        lookup_result=lookup_result,
        inbound_reactions=True,
    )
    # Configure get_active_session to return a mock session with the
    # desired last_exchange, matching the production async API.
    active_mock = MagicMock(
        spec=Session, id=active_session_id, last_exchange=last_exchange,
    )
    registry.get_active_session.return_value = active_mock
    return channel, registry


class TestReactionSessionRouting:
    """R4/R6: Reaction handler looks up external ID and routes accordingly."""

    async def test_different_session_defers_with_target(self) -> None:
        """R4: Reaction on message from different session defers with target_session_id."""
        channel, registry = _make_reaction_channel(
            lookup_result="session-past",
        )

        event = _make_reaction_event(
            message_id=99,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        # Should call enqueue_deferred, not enqueue
        channel._coordinator.enqueue_deferred.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.target_session_id == "session-past"
        assert envelope.external_id == "99"
        assert envelope.added == frozenset({"👍"})

        # Verify lookup was called with correct args
        registry.find_session_by_external_id.assert_called_once_with("telegram", "99")

    async def test_same_session_routes_normally(self) -> None:
        """R4: Reaction on message from current session routes normally."""
        channel, registry = _make_reaction_channel(
            active_session_id="session-current",
            lookup_result="session-current",
        )

        event = _make_reaction_event(
            message_id=50,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert isinstance(envelope, ReactionMessage)
        assert envelope.target_session_id is None

    async def test_unknown_external_id_notifies_user(self) -> None:
        """R1: Unknown external ID → notification sent, reaction dropped."""
        channel, registry = _make_reaction_channel(
            lookup_result=None,
        )

        event = _make_reaction_event(
            message_id=999,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        # Notification sent, reaction dropped
        channel._bot.send_message.assert_called_once()
        channel._coordinator.enqueue.assert_not_called()
        channel._coordinator.enqueue_deferred.assert_not_called()

    async def test_no_registry_routes_normally(self) -> None:
        """R6: No registry available — graceful degradation."""
        channel = _make_channel()
        channel._coordinator._registry = None
        channel._process_through_coordinator = AsyncMock()
        channel._drain_deferred_queue = AsyncMock()

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None
        assert envelope.external_id == "42"

    async def test_lookup_failure_routes_normally(self) -> None:
        """R6: Registry lookup raises — graceful degradation."""
        channel, registry = _make_reaction_channel()
        registry.find_session_by_external_id.side_effect = RuntimeError("db error")

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None

    async def test_busy_reaction_steers_without_session_switch(self) -> None:
        """R4 AC: Mid-stream reaction steers into current session, no session switch."""
        channel, registry = _make_reaction_channel(
            lookup_result="session-past",
        )

        await channel._delivery_lock.acquire()
        try:
            event = _make_reaction_event(
                message_id=99,
                new_reaction=[_make_emoji("👍")],
            )
            await asyncio.wait_for(channel._handle_reaction(event), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        channel._coordinator.enqueue.assert_called_once()
        channel._coordinator.enqueue_deferred.assert_not_called()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None
        assert envelope.external_id == "99"

    async def test_external_id_set_on_envelope(self) -> None:
        """R2/R3: external_id is set to str(event.message_id)."""
        channel, _ = _make_reaction_channel(lookup_result="session-current")

        event = _make_reaction_event(
            message_id=123,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.external_id == "123"

    async def test_no_active_session_routes_normally(self) -> None:
        """R6: Lookup returns a target but no active session — no switch."""
        channel, registry = _make_reaction_channel(lookup_result="session-current")
        registry.get_active_session.return_value = None

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._coordinator.enqueue.assert_called_once()
        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.target_session_id is None


class TestReactionNotificationOnNotFound:
    """R1: User is notified when reaction target session not found (idle path)."""

    async def test_not_found_sends_notification(self) -> None:
        """AC1: Idle + not-found → notification sent, reaction dropped."""
        channel, registry = _make_reaction_channel(lookup_result=None)

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        # Notification sent
        channel._bot.send_message.assert_called_once()
        call_text = channel._bot.send_message.call_args[0][1]
        assert "couldn't find the conversation" in call_text

        # Reaction dropped (not enqueued)
        channel._coordinator.enqueue.assert_not_called()
        channel._coordinator.enqueue_deferred.assert_not_called()

    async def test_not_found_no_notification_when_busy(self) -> None:
        """AC2: Busy + not-found → no notification, reaction steered."""
        channel, registry = _make_reaction_channel(lookup_result=None)

        await channel._delivery_lock.acquire()
        try:
            event = _make_reaction_event(
                message_id=42,
                new_reaction=[_make_emoji("👍")],
            )
            await asyncio.wait_for(channel._handle_reaction(event), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        # No notification
        channel._bot.send_message.assert_not_called()

        # Reaction steered into active session
        channel._coordinator.enqueue.assert_called_once()

    async def test_found_no_notification(self) -> None:
        """No notification when session IS found."""
        channel, _ = _make_reaction_channel(lookup_result="session-current")

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        channel._bot.send_message.assert_not_called()
        channel._coordinator.enqueue.assert_called_once()


class TestReactionContextPrefix:
    """R2: Context prefix from last_exchange for current-session reactions."""

    async def test_prefix_set_from_last_exchange(self) -> None:
        """AC3: Active session with last_exchange → prefix set on envelope."""
        channel, _ = _make_reaction_channel(
            lookup_result="session-current",
            last_exchange="Here is my response to your question.",
        )

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.message_prefix == "Reacted to:\n> Here is my response to your question."

    async def test_prefix_truncates_long_last_exchange(self) -> None:
        """AC3: Long last_exchange is truncated via _truncate_reply_text."""
        long_text = "x" * 300
        channel, _ = _make_reaction_channel(
            lookup_result="session-current",
            last_exchange=long_text,
        )

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.message_prefix is not None
        assert envelope.message_prefix.startswith("Reacted to:\n> ")
        # 300 chars → truncated to first 100 + [...] + last 100
        prefix_text = envelope.message_prefix.split("> ", 1)[1]
        assert "[...]" in prefix_text

    async def test_no_prefix_when_no_active_session(self) -> None:
        """AC4: No active session → message_prefix is None."""
        channel, registry = _make_reaction_channel(lookup_result="session-current")
        registry.get_active_session.return_value = None

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.message_prefix is None

    async def test_no_prefix_when_no_last_exchange(self) -> None:
        """AC4: Active session but no last_exchange → message_prefix is None."""
        channel, _ = _make_reaction_channel(lookup_result="session-current", last_exchange=None)

        event = _make_reaction_event(
            message_id=42,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.message_prefix is None

    async def test_no_prefix_for_cross_session_reaction(self) -> None:
        """AC6: Reaction deferred for session switch → no prefix."""
        channel, _ = _make_reaction_channel(
            lookup_result="session-past",
            last_exchange="This should not appear.",
        )

        event = _make_reaction_event(
            message_id=99,
            new_reaction=[_make_emoji("👍")],
        )
        await channel._handle_reaction(event)

        envelope = channel._coordinator.enqueue_deferred.call_args[0][0]
        assert envelope.message_prefix is None

    async def test_no_prefix_when_busy(self) -> None:
        """Mid-stream reaction → no prefix (agent has full context)."""
        channel, _ = _make_reaction_channel(
            last_exchange="This should not appear.",
        )

        await channel._delivery_lock.acquire()
        try:
            event = _make_reaction_event(
                message_id=42,
                new_reaction=[_make_emoji("👍")],
            )
            await asyncio.wait_for(channel._handle_reaction(event), timeout=0.05)
        finally:
            channel._delivery_lock.release()

        envelope = channel._coordinator.enqueue.call_args[0][0]
        assert envelope.message_prefix is None
