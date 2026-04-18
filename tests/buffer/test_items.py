from unittest.mock import AsyncMock

from tachikoma.buffer.items import BufferedItem
from tachikoma.buffer.priority import Priority


class _FakeNotification:
    def __init__(
        self,
        prompt: str,
        source_id: str | None = None,
        priority: Priority = Priority.NORMAL,
    ) -> None:
        self.prompt = prompt
        self.source_id = source_id
        self.priority = priority


class _FakeTaskInstance:
    def __init__(self, id: str, prompt: str) -> None:
        self.id = id
        self.prompt = prompt


class TestBufferedItem:
    def test_from_notification_defaults(self) -> None:
        event = _FakeNotification(prompt="hello", source_id="task:1")

        item = BufferedItem.from_notification(event)

        assert item.kind == "notification"
        assert item.prompt == "hello"
        assert item.source_id == "task:1"
        assert item.priority == Priority.NORMAL
        assert item.on_delivered is None

    def test_from_notification_with_priority(self) -> None:
        event = _FakeNotification(prompt="urgent!", priority=Priority.URGENT)

        item = BufferedItem.from_notification(event)

        assert item.priority == Priority.URGENT

    def test_from_session_instance(self) -> None:
        instance = _FakeTaskInstance(id="inst-1", prompt="run daily digest")
        callback = AsyncMock()

        item = BufferedItem.from_session_instance(instance, on_delivered=callback)

        assert item.kind == "session_task"
        assert item.prompt == "run daily digest"
        assert item.source_id == "inst-1"
        assert item.priority == Priority.NORMAL
        assert item.on_delivered is callback
        assert item.metadata["instance"] is instance

    def test_default_fields(self) -> None:
        item = BufferedItem(
            priority=Priority.LOW,
            prompt="test",
            kind="notification",
        )

        assert item.arrival_seq == 0
        assert item.total_front_time == 0.0
        assert item.current_front_since is None
        assert item.source_id is None
        assert item.metadata == {}
