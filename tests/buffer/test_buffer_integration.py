"""Integration test: Notification → Buffer → BufferedDelivery."""

import asyncio
from datetime import UTC, datetime, timedelta

from bubus import EventBus

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.events import BufferedDelivery
from tachikoma.buffer.priority import Priority
from tachikoma.config import BufferSettings, PriorityTiming
from tachikoma.notifications import Notification


class FakeCoordinator:
    def __init__(self) -> None:
        self._last_msg_time = datetime.now(UTC) - timedelta(seconds=10)

    @property
    def _is_busy(self) -> bool:
        return False

    @property
    def last_message_time(self) -> datetime | None:
        return self._last_msg_time


def _tight_settings() -> BufferSettings:
    return BufferSettings(
        urgent=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.2),
        normal=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=0.4),
        low=PriorityTiming(idle_window_seconds=0.05, max_hold_seconds=None),
    )


class TestNotificationToDelivery:
    async def test_notification_flows_through_buffer(self) -> None:
        """Dispatch Notification → Buffer subscribes → delivers BufferedDelivery."""
        bus = EventBus()
        coord = FakeCoordinator()
        buf = Buffer(bus=bus, coordinator=coord, settings=_tight_settings())

        delivered: list[BufferedDelivery] = []

        async def capture(event: BufferedDelivery) -> None:
            delivered.append(event)

        bus.on(BufferedDelivery, capture)
        bus.on(Notification, buf._handle_notification)
        await buf.start()

        try:
            bus.dispatch(
                Notification(prompt="Integration test message", priority=Priority.NORMAL),
            )

            await asyncio.sleep(0.3)

            assert len(delivered) == 1
            assert delivered[0].items[0].kind == "notification"
            assert "Integration test message" in delivered[0].items[0].prompt
        finally:
            await buf.stop()
            await bus.stop()
