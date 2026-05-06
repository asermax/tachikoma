"""Event type registry: auto-discovers BaseEvent subclasses and maps snake_case names."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from bubus import BaseEvent

_log = logger.bind(component="plugins")

_CAMEL_TO_SNAKE = re.compile(r"(?<!^)(?=[A-Z])")

_REGISTRY: dict[str, type[BaseEvent]] | None = None


def build_event_registry() -> dict[str, type[BaseEvent]]:
    """Build a mapping of snake_case event names to BaseEvent subclasses.

    Walks ``BaseEvent.__subclasses__()`` recursively and derives snake_case
    names from CamelCase class names. Imports all known event modules before
    walking so every subclass is discovered.
    """
    from bubus import BaseEvent  # noqa: PLC0415

    # Import all event modules so __subclasses__() finds every event type.
    from tachikoma.buffer import events as _buffer_events  # noqa: F401, PLC0415
    from tachikoma.notifications import Notification as _Notification  # noqa: F401, PLC0415
    from tachikoma.plugins import events as _plugin_events  # noqa: F401, PLC0415
    from tachikoma.skills import events as _skills_events  # noqa: F401, PLC0415
    from tachikoma.updates import events as _updates_events  # noqa: F401, PLC0415

    global _REGISTRY  # noqa: PLW0603
    if _REGISTRY is not None:
        return _REGISTRY

    registry: dict[str, type[BaseEvent]] = {}

    def _walk(cls: type[BaseEvent]) -> None:
        for sub in cls.__subclasses__():
            name = _CAMEL_TO_SNAKE.sub("_", sub.__name__).lower()
            registry[name] = sub
            _walk(sub)

    _walk(BaseEvent)
    _REGISTRY = registry
    _log.debug("Event registry built: {} types", len(registry))
    return registry


def get_event_type(name: str) -> type[BaseEvent]:
    """Look up an event type by snake_case name.

    Raises ``KeyError`` with valid names listed if *name* is unknown.
    """
    registry = build_event_registry()
    if name not in registry:
        valid = ", ".join(sorted(registry.keys()))
        raise KeyError(f"Unknown event type '{name}'. Valid types: {valid}")
    return registry[name]
