"""Factory helper for constructing and wiring the Buffer subsystem."""

from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.events import CoordinatorIdle
from tachikoma.config import BufferSettings
from tachikoma.notifications import Notification

if TYPE_CHECKING:
    from bubus import EventBus

    from tachikoma.coordinator import Coordinator

_log = logger.bind(component="buffer")


async def create_and_start_buffer(
    *,
    bus: "EventBus",
    coordinator: "Coordinator",
    settings: BufferSettings,
) -> Buffer:
    """Construct the Buffer, subscribe handlers, and start the loop."""
    buffer = Buffer(bus=bus, coordinator=coordinator, settings=settings)

    bus.on(Notification, buffer._handle_notification)
    bus.on(CoordinatorIdle, buffer._handle_coordinator_idle)

    await buffer.start()

    _log.info("Buffer subsystem online")
    return buffer
