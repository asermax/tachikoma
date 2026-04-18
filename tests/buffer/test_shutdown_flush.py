"""Tests for Buffer shutdown flush."""

import asyncio
from datetime import UTC, datetime, timedelta

from bubus import EventBus

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.events import BufferedDelivery
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority
from tachikoma.config import BufferSettings, PriorityTiming


class FakeCoordinator:
    def __init__(self) -> None:
        self._busy = False
        self._last_msg_time = datetime.now(UTC) - timedelta(seconds=10)

    @property
    def _is_busy(self) -> bool:
        return self._busy

    @property
    def last_message_time(self) -> datetime | None:
        return self._last_msg_time


def _tight_settings() -> BufferSettings:
    return BufferSettings(
        urgent=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.2),
        normal=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.4),
        low=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=None),
    )


def _make_item(
    priority: Priority = Priority.NORMAL,
    prompt: str = "test",
    kind: str = "notification",
) -> BufferedItem:
    return BufferedItem(priority=priority, prompt=prompt, kind=kind)


class TestFlushOnShutdown:
    async def test_empty_buffer_returns_immediately(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        dispatched: list[BufferedDelivery] = []
        bus.dispatch = lambda e: dispatched.append(e)  # type: ignore[assignment]

        # Should return immediately without dispatching anything
        await buf.flush_on_shutdown()

        assert len(dispatched) == 0

    async def test_non_empty_buffer_dispatches_digest(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.enqueue(_make_item(Priority.NORMAL, prompt="item1"))
        await buf.enqueue(_make_item(Priority.LOW, prompt="item2"))

        dispatched: list[BufferedDelivery] = []

        async def capture_and_resolve(event: BufferedDelivery) -> None:
            dispatched.append(event)
            # Resolve immediately so flush_on_shutdown doesn't hang
            buf.resolve_shutdown()

        bus.dispatch = lambda e: capture_and_resolve(e)  # type: ignore[assignment]

        await buf.flush_on_shutdown()

        assert len(dispatched) == 1
        assert dispatched[0].is_shutdown_digest is True
        assert len(dispatched[0].items) == 2
        assert "Shutdown digest" in dispatched[0].prompt

    async def test_flush_blocks_until_resolved(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.enqueue(_make_item())

        resolved = False

        async def dispatch_and_delay_resolve(event: BufferedDelivery) -> None:
            await asyncio.sleep(0.05)
            nonlocal resolved
            buf.resolve_shutdown()
            resolved = True

        bus.dispatch = lambda e: dispatch_and_delay_resolve(e)  # type: ignore[assignment]

        await buf.flush_on_shutdown()

        assert resolved is True

    async def test_dispatch_error_handled_gracefully(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.enqueue(_make_item())

        def bad_dispatch(event: BufferedDelivery) -> None:
            raise RuntimeError("dispatch failed")

        bus.dispatch = bad_dispatch  # type: ignore[assignment]

        # Should not raise
        await buf.flush_on_shutdown()

    async def test_resolve_shutdown_is_idempotent(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        buf.resolve_shutdown()  # No-op when no flush pending
        buf.resolve_shutdown()  # Still no-op
