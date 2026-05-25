"""Tests for the message_reaction handler in TelegramChannel.

Covers: emoji filtering, diff computation, routing (busy/idle),
auth filter, empty-diff early return, inbound_reactions config gating.
"""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import ReactionTypeCustomEmoji, ReactionTypeEmoji, ReactionTypePaid

from tachikoma.message import ReactionMessage
from tachikoma.telegram import TelegramChannel, _emoji_set


def _make_channel(
    authorized_chat_id: int = 123,
    inbound_reactions: bool = True,
) -> TelegramChannel:
    """Build a TelegramChannel with mocked dependencies."""
    coordinator = MagicMock()
    coordinator.has_deferred = False
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
        coordinator = MagicMock()
        # Without this, run()'s finally-block drain spins on a truthy MagicMock attr.
        coordinator.has_deferred = False

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
        coordinator = MagicMock()
        # Without this, run()'s finally-block drain spins on a truthy MagicMock attr.
        coordinator.has_deferred = False

        with patch.object(
            channel._dispatcher, "start_polling", new_callable=AsyncMock
        ) as mock_poll:
            mock_poll.side_effect = KeyboardInterrupt
            with contextlib.suppress(KeyboardInterrupt):
                await channel.run(coordinator)

        kwargs = mock_poll.call_args.kwargs
        assert "message_reaction" not in kwargs["allowed_updates"]
