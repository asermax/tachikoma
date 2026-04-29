"""Plugin lifecycle events dispatched on the bubus EventBus.

Three events cover the install/remove lifecycle:
- ``PluginInstalled`` — dispatched after a plugin is fully installed (materialized,
  manifest parsed, config written). Subscribers register plugin skills.
- ``PluginRemoving`` — dispatched *before* directory deletion so subscribers can
  access still-valid on-disk paths (e.g., to update active session entries).
- ``PluginRemoved`` — dispatched after cleanup. Subscribers remove skills from
  the registry.

Design reference: DLT-048 S11.
"""

from __future__ import annotations

from typing import Any

from bubus import BaseEvent


class PluginInstalled(BaseEvent[None]):
    """Dispatched after a plugin has been fully installed.

    Subscribers should register the plugin's skill directories into the
    skill registry.
    """

    alias: str
    plugin: Any  # LoadedPlugin — Any avoids Pydantic forward-ref issues


class PluginRemoving(BaseEvent[None]):
    """Dispatched before a plugin's install directory is deleted.

    Subscribers can access still-valid on-disk paths. Used to mark active
    session context entries as deleted.
    """

    alias: str
    namespaced_skill_names: list[str]


class PluginRemoved(BaseEvent[None]):
    """Dispatched after a plugin's install directory has been cleaned up.

    Subscribers should remove the plugin's namespaced skills from the
    skill registry.
    """

    alias: str
