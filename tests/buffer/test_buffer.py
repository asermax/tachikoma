"""Tests for the Buffer core (priority queue + event-driven loop).

DLT-112: Unified priority buffer for deferred notification and session task delivery.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from bubus import EventBus

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.events import BufferedDelivery
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority
from tachikoma.config import BufferSettings, PriorityTiming


class FakeCoordinator:
    """Minimal coordinator stub for buffer tests."""

    def __init__(
        self,
        is_busy: bool = False,
        last_message_time: datetime | None = None,
    ) -> None:
        self._busy = is_busy
        self._last_msg_time = last_message_time

    @property
    def _is_busy(self) -> bool:
        return self._busy

    @property
    def last_message_time(self) -> datetime | None:
        return self._last_msg_time

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def set_last_message_time(self, t: datetime | None) -> None:
        self._last_msg_time = t


def _tight_settings() -> BufferSettings:
    """Buffer settings with small windows for fast tests."""
    return BufferSettings(
        urgent=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.2),
        normal=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.4),
        low=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=None),
    )


def _make_item(
    priority: Priority = Priority.NORMAL,
    kind: str = "notification",
    prompt: str = "test",
) -> BufferedItem:
    return BufferedItem(priority=priority, prompt=prompt, kind=kind)


class TestBufferConstruction:
    def test_empty_heap_on_init(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        assert len(buf._heap) == 0
        assert buf._loop_task is None


class TestBufferEnqueue:
    async def test_enqueue_sets_arrival_seq(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        item1 = _make_item()
        item2 = _make_item()
        await buf.enqueue(item1)
        await buf.enqueue(item2)

        assert item1.arrival_seq < item2.arrival_seq

    async def test_enqueue_wakes_event(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.enqueue(_make_item())
        assert buf._wake_event.is_set()

    async def test_priority_ordering(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        low = _make_item(Priority.LOW, prompt="low")
        normal = _make_item(Priority.NORMAL, prompt="normal")
        urgent = _make_item(Priority.URGENT, prompt="urgent")

        await buf.enqueue(normal)
        await buf.enqueue(low)
        await buf.enqueue(urgent)

        # Check heap order: urgent first
        assert buf._heap[0][0] == Priority.URGENT.value


class TestBufferDelivery:
    async def test_delivers_after_idle_window(self) -> None:
        """Normal item delivered once idle window is satisfied."""
        bus = EventBus()
        old_time = datetime.now(UTC) - timedelta(seconds=10)
        coord = FakeCoordinator(last_message_time=old_time)
        settings = _tight_settings()
        buf = Buffer(bus=bus, coordinator=coord, settings=settings)

        delivered: list[BufferedDelivery] = []

        async def capture(event: BufferedDelivery) -> None:
            delivered.append(event)

        bus.dispatch = lambda e: capture(e)  # type: ignore[assignment]

        await buf.start()
        await buf.enqueue(_make_item(Priority.NORMAL, prompt="hello"))

        # Wait for delivery (idle window is 0.05s, already satisfied)
        await asyncio.sleep(0.2)
        await buf.stop()

        assert len(delivered) == 1
        assert delivered[0].items[0].prompt == "hello"

    async def test_urgent_delivered_before_normal(self) -> None:
        """Urgent items are delivered first when both are queued."""
        bus = EventBus()
        old_time = datetime.now(UTC) - timedelta(seconds=10)
        coord = FakeCoordinator(last_message_time=old_time)
        settings = _tight_settings()
        buf = Buffer(bus=bus, coordinator=coord, settings=settings)

        delivered_prompts: list[str] = []

        async def capture(event: BufferedDelivery) -> None:
            delivered_prompts.append(event.items[0].prompt)

        bus.dispatch = lambda e: capture(e)  # type: ignore[assignment]

        await buf.start()
        await buf.enqueue(_make_item(Priority.NORMAL, prompt="normal"))
        await buf.enqueue(_make_item(Priority.URGENT, prompt="urgent"))

        await asyncio.sleep(0.3)
        await buf.stop()

        assert delivered_prompts[0] == "urgent"

    async def test_no_delivery_when_busy(self) -> None:
        """Buffer does not deliver when coordinator is busy."""
        bus = EventBus()
        old_time = datetime.now(UTC) - timedelta(seconds=10)
        coord = FakeCoordinator(is_busy=True, last_message_time=old_time)
        settings = _tight_settings()
        buf = Buffer(bus=bus, coordinator=coord, settings=settings)

        delivered: list[BufferedDelivery] = []

        bus.dispatch = lambda e: delivered.append(e)  # type: ignore[assignment]

        await buf.start()
        await buf.enqueue(_make_item())

        await asyncio.sleep(0.1)
        assert len(delivered) == 0

        # Now make idle
        coord.set_busy(False)
        buf._wake_event.set()

        await asyncio.sleep(0.2)
        await buf.stop()

        assert len(delivered) == 1

    async def test_max_hold_force_delivery(self) -> None:
        """Item force-delivered after max hold period even with stale last_message_time."""
        bus = EventBus()
        # Recent time so idle window is NOT satisfied
        recent_time = datetime.now(UTC)
        coord = FakeCoordinator(last_message_time=recent_time)
        settings = BufferSettings(
            normal=PriorityTiming(idle_window_seconds=999, max_hold_seconds=0.1),
        )
        buf = Buffer(bus=bus, coordinator=coord, settings=settings)

        delivered: list[BufferedDelivery] = []

        bus.dispatch = lambda e: delivered.append(e)  # type: ignore[assignment]

        await buf.start()
        await buf.enqueue(_make_item())

        await asyncio.sleep(0.3)
        await buf.stop()

        assert len(delivered) == 1


class TestBufferLifecycle:
    async def test_start_stop(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.start()
        assert buf._loop_task is not None

        await buf.stop()
        assert buf._loop_task is None

    async def test_stop_without_start_is_noop(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        await buf.stop()  # Should not raise
        assert buf._loop_task is None
