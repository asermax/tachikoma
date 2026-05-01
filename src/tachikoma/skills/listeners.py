"""Skills-event listener for plugin lifecycle events.

Subscribes to ``PluginInstalled``, ``PluginRemoving``, and ``PluginRemoved``
and translates them into skill-registry mutations and ``SkillsChanged``
dispatches. The plugin manager itself has no skill-registry coupling — all
registry mutation is owned here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.skills.events import SkillsChanged

if TYPE_CHECKING:
    from bubus import EventBus

    from tachikoma.sessions.registry import SessionRegistry
    from tachikoma.skills.registry import SkillRegistry

_log = logger.bind(component="skills.listeners")

_REMOVAL_NOTICE_TEMPLATE = (
    "**[Plugin removed: '{alias}'. "
    "Files under .tachikoma/plugins/{alias}/ may no longer be available on disk.]**\n\n"
)


def register_plugin_event_listeners(
    bus: EventBus,
    registry: SkillRegistry,
    session_registry: SessionRegistry,
) -> None:
    """Subscribe handlers for the three plugin lifecycle events.

    Each handler closes over the provided dependencies so no module-level
    mutable state is needed.

    Args:
        bus: The bubus EventBus to subscribe on.
        registry: The skill registry to mutate on install/remove.
        session_registry: The session registry for active-session entry updates.
    """

    async def on_plugin_installed(event: PluginInstalled) -> None:
        alias = event.alias
        plugin = event.plugin

        if plugin.manifest is None:
            return

        for skill_dir in plugin.manifest.skill_dirs:
            registry.add_namespaced_source(alias, skill_dir)

        # Populate contributed_skills on the LoadedPlugin instance.
        # The manager holds the same dataclass reference — in-place mutation
        # of the mutable list field is intentional (frozen dataclasses still
        # permit in-place mutation of mutable field values).
        plugin.contributed_skills.extend(
            s for s in registry.skills.values() if s.namespace == alias
        )

        await bus.dispatch(SkillsChanged())

        _log.info(
            "Plugin skills registered: alias={alias} count={count}",
            alias=alias,
            count=len(plugin.contributed_skills),
        )

    async def on_plugin_removing(event: PluginRemoving) -> None:
        active = await session_registry.get_active_session()
        if active is None:
            _log.debug(
                "No active session — skipping entry marking for PluginRemoving: alias={alias}",
                alias=event.alias,
            )
            return

        entries = await session_registry.find_context_entries_by_skill_name(
            active.id, event.namespaced_skill_names
        )

        notice = _REMOVAL_NOTICE_TEMPLATE.format(alias=event.alias)

        for entry in entries:
            new_content = notice + entry.content
            new_metadata = dict(entry.metadata) if entry.metadata else {}
            new_metadata["deleted"] = True

            result = await session_registry.update_context_entry(
                entry.id, content=new_content, metadata=new_metadata
            )
            if result is None:
                _log.debug(
                    "Context entry vanished during PluginRemoving handling: entry_id={id}",
                    id=entry.id,
                )

        _log.info(
            "Marked session entries for plugin removal: alias={alias} entries={count}",
            alias=event.alias,
            count=len(entries),
        )

    async def on_plugin_removed(event: PluginRemoved) -> None:
        registry.remove_namespaced_source(event.alias)
        await bus.dispatch(SkillsChanged())

        _log.info(
            "Plugin skills removed from registry: alias={alias}",
            alias=event.alias,
        )

    bus.on(PluginInstalled, on_plugin_installed)
    bus.on(PluginRemoving, on_plugin_removing)
    bus.on(PluginRemoved, on_plugin_removed)
