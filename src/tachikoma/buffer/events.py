from datetime import datetime

from bubus import BaseEvent

from tachikoma.buffer.items import BufferedItem


class CoordinatorIdle(BaseEvent[None]):
    """Dispatched by the coordinator exactly once per busy->idle transition."""

    timestamp: datetime


class BufferedDelivery(BaseEvent[None]):
    """Dispatched when buffered items are ready for delivery.

    For normal delivery, contains a single item. For shutdown flush,
    contains multiple items with ``is_shutdown_digest=True``.
    """

    prompt: str
    items: list[BufferedItem]
    is_shutdown_digest: bool = False

    def pinned_skills(self) -> tuple[str, ...]:
        """Collect pinned skill names from session_task items."""
        pinned: list[str] = []
        for item in self.items:
            if item.kind == "session_task":
                pinned.extend(item.metadata.get("pinned_skills", []))
        return tuple(pinned)
