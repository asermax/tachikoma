"""Update subsystem events."""

from bubus import BaseEvent


class RestartRequested(BaseEvent[None]):
    """Dispatched to perform an in-place process restart."""
