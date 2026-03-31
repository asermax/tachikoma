"""Typed event classes for the task subsystem.

Events are dispatched on the bubus EventBus and consumed by channels
and other subsystems.
"""

from collections.abc import Awaitable, Callable
from typing import Literal

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


class TaskNotification(BaseEvent[None]):
    """Event dispatched when a background task completes or fails.

    Channels subscribe to this event and enqueue the prompt into the coordinator
    for delivery through the standard message processing pipeline.
    """

    prompt: str = Field(
        description="The notification prompt to route through the coordinator pipeline"
    )

    source_task_id: str | None = Field(
        default=None,
        description="ID of the task instance that triggered this notification",
    )

    severity: Literal["info", "error"] = Field(
        default="info",
        description="Severity level: info for success, error for failures",
    )
