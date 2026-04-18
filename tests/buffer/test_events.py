from datetime import UTC, datetime

from bubus import BaseEvent

from tachikoma.buffer.events import BufferedDelivery, CoordinatorIdle
from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority


class TestCoordinatorIdle:
    def test_is_base_event(self) -> None:
        assert issubclass(CoordinatorIdle, BaseEvent)

    def test_instantiation(self) -> None:
        ts = datetime.now(UTC)
        event = CoordinatorIdle(timestamp=ts)

        assert event.timestamp == ts


class TestBufferedDelivery:
    def test_is_base_event(self) -> None:
        assert issubclass(BufferedDelivery, BaseEvent)

    def test_single_item_delivery(self) -> None:
        item = BufferedItem(
            priority=Priority.NORMAL,
            prompt="hello",
            kind="notification",
        )
        event = BufferedDelivery(prompt="hello", items=[item])

        assert event.prompt == "hello"
        assert len(event.items) == 1
        assert event.is_shutdown_digest is False

    def test_shutdown_digest(self) -> None:
        items = [
            BufferedItem(priority=Priority.URGENT, prompt="a", kind="notification"),
            BufferedItem(priority=Priority.NORMAL, prompt="b", kind="session_task"),
        ]
        event = BufferedDelivery(
            prompt="digest",
            items=items,
            is_shutdown_digest=True,
        )

        assert event.is_shutdown_digest is True
        assert len(event.items) == 2
