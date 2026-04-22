"""Update subsystem events."""

from bubus import BaseEvent


class RestartRequested(BaseEvent[None]):
    """Dispatched when the process should restart after a successful upgrade."""
