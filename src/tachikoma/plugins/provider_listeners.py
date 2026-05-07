"""Plugin provider lifecycle listeners.

Subscribes to ``PluginInstalled`` and ``PluginRemoving`` and translates
them into pipeline register/unregister calls.  The plugin manager has no
pipeline coupling — all provider→pipeline wiring is owned here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.plugins.events import PluginInstalled, PluginRemoving

if TYPE_CHECKING:
    from bubus import EventBus

    from tachikoma.per_message_pre_processing import MessagePreProcessingPipeline
    from tachikoma.plugins.manager import PluginManager
    from tachikoma.post_processing import PostProcessingPipeline
    from tachikoma.pre_processing import PreProcessingPipeline

_log = logger.bind(component="plugins.provider_listeners")


def register_plugin_provider_listeners(
    bus: EventBus,
    pre_pipeline: PreProcessingPipeline,
    msg_pre_pipeline: MessagePreProcessingPipeline,
    plugin_manager: PluginManager,
    post_pipeline: PostProcessingPipeline,
) -> None:
    """Subscribe handlers for provider pipeline registration lifecycle events.

    Args:
        bus: The bubus EventBus to subscribe on.
        pre_pipeline: Session-gated pre-processing pipeline for ContextProvider registration.
        msg_pre_pipeline: Per-message pipeline for MessageContextProvider registration.
        plugin_manager: Plugin manager for looking up LoadedPlugin instances during removal.
        post_pipeline: Post-processing pipeline for PostProcessor registration.
    """

    async def on_plugin_installed(event: PluginInstalled) -> None:
        plugin = event.plugin

        for provider in plugin.context_providers:
            pre_pipeline.register(provider)

        for provider in plugin.message_context_providers:
            msg_pre_pipeline.register(provider)

        for processor in plugin.post_processors:
            post_pipeline.register(processor)

        _log.info(
            "Plugin providers registered: alias={alias} "
            "session={session_count} message={msg_count} post={post_count}",
            alias=event.alias,
            session_count=len(plugin.context_providers),
            msg_count=len(plugin.message_context_providers),
            post_count=len(plugin.post_processors),
        )

    async def on_plugin_removing(event: PluginRemoving) -> None:
        loaded = plugin_manager.loaded_plugins()
        plugin = loaded.get(event.alias)

        if plugin is None:
            _log.debug(
                "Plugin not found during removal — skipping provider cleanup: alias={alias}",
                alias=event.alias,
            )
            return

        for provider in plugin.context_providers:
            pre_pipeline.unregister(provider)

        for provider in plugin.message_context_providers:
            msg_pre_pipeline.unregister(provider)

        for processor in plugin.post_processors:
            post_pipeline.unregister(processor)

        _log.info(
            "Plugin providers unregistered: alias={alias} "
            "session={session_count} message={msg_count} post={post_count}",
            alias=event.alias,
            session_count=len(plugin.context_providers),
            msg_count=len(plugin.message_context_providers),
            post_count=len(plugin.post_processors),
        )

    bus.on(PluginInstalled, on_plugin_installed)
    bus.on(PluginRemoving, on_plugin_removing)
