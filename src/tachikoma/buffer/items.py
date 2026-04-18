from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from tachikoma.buffer.priority import Priority


@dataclass
class BufferedItem:
    """A single item in the priority buffer.

    Unified model for notifications and session tasks, discriminated
    by ``kind``. The ``on_delivered`` callback fires after the channel
    successfully routes the item through the coordinator.
    """

    priority: Priority
    prompt: str
    kind: Literal["notification", "session_task"]
    source_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    on_delivered: Callable[[], Awaitable[None]] | None = None

    # Assigned on enqueue by the Buffer
    arrival_seq: int = 0
    total_front_time: float = 0.0
    current_front_since: datetime | None = None

    @classmethod
    def from_notification(cls, event: Any) -> BufferedItem:
        """Build a BufferedItem from a Notification event."""
        priority = getattr(event, "priority", Priority.NORMAL)

        return cls(
            priority=priority,
            prompt=event.prompt,
            kind="notification",
            source_id=event.source_id,
        )

    @classmethod
    def from_session_instance(
        cls,
        instance: Any,
        on_delivered: Callable[[], Awaitable[None]] | None = None,
    ) -> BufferedItem:
        """Build a BufferedItem from a TaskInstance for session-task delivery."""
        return cls(
            priority=Priority.NORMAL,
            prompt=instance.prompt,
            kind="session_task",
            source_id=instance.id,
            metadata={"instance": instance},
            on_delivered=on_delivered,
        )
