"""Tests for the buffer factory helper."""

import asyncio
from datetime import UTC, datetime, timedelta

from bubus import EventBus

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.events import BufferedDelivery
from tachikoma.buffer.factory import create_and_start_buffer
from tachikoma.buffer.priority import Priority
from tachikoma.config import BufferSettings, PriorityTiming
from tachikoma.notifications import Notification


class FakeCoordinator:
    """Minimal coordinator stub for factory tests."""

    def __init__(self) -> None:
        self._busy = False
        self._last_msg_time: datetime | None = datetime.now(UTC) - timedelta(seconds=10)

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


class TestCreateAndStartBuffer:
    async def test_returns_started_buffer(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = await create_and_start_buffer(
            bus=bus,
            coordinator=coord,
            settings=_tight_settings(),
        )

        try:
            assert isinstance(buf, Buffer)
            assert buf._loop_task is not None
        finally:
            await buf.stop()
            await bus.stop()

    async def test_notification_subscription_works(self) -> None:
        bus = EventBus()
        coord = FakeCoordinator()
        buf = await create_and_start_buffer(
            bus=bus,
            coordinator=coord,
            settings=_tight_settings(),
        )

        delivered: list[BufferedDelivery] = []

        async def capture(event: BufferedDelivery) -> None:
            delivered.append(event)

        bus.on(BufferedDelivery, capture)

        try:
            bus.dispatch(
                Notification(prompt="test notification", priority=Priority.NORMAL),
            )

            await asyncio.sleep(0.3)

            assert len(delivered) == 1
            assert delivered[0].items[0].prompt == "test notification"
            assert delivered[0].items[0].kind == "notification"
        finally:
            await buf.stop()
            await bus.stop()
