"""Tests for the message_reaction handler in TelegramChannel.

Covers: emoji filtering, diff computation, routing (busy/idle),
auth filter, empty-diff early return, inbound_reactions config gating.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import ReactionTypeEmoji, ReactionTypePaid

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

    def test_empty_list(self) -> None:
        assert _emoji_set([]) == frozenset()


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
