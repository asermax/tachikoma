"""Typed event classes for the task subsystem.

Events are dispatched on the bubus EventBus and consumed by channels
and other subsystems.
"""

from collections.abc import Awaitable, Callable

from bubus import BaseEvent
from pydantic import Field

from tachikoma.tasks.model import TaskInstance


class SessionTaskReady(BaseEvent[None]):
    """Event dispatched when a session task is ready for delivery.

    Channels subscribe to this event to receive proactive messages
    to send to the user during idle time.
    """

    instance: TaskInstance = Field(description="The task instance to deliver")

    on_complete: Callable[[], Awaitable[None]] | None = Field(
        default=None,
        exclude=True,
        description="Callback to invoke after successful delivery",
    )
