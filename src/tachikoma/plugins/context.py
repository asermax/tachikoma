"""Plugin context: frozen dataclass provided to init hooks and event handlers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bubus import EventBus


@dataclass(frozen=True)
class PluginContext:
    """Immutable context provided to plugin init hooks and event handlers.

    Attributes:
        config: The plugin's validated configuration values.
        event_bus: Reference to the application event bus (subscribe only).
        alias: The plugin's alias.
        install_path: Absolute path to the plugin install directory.
    """

    config: dict[str, str | int | bool | float]
    event_bus: EventBus
    alias: str
    install_path: Path
