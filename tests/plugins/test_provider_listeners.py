"""Tests for plugin provider lifecycle listeners.

Covers acceptance criteria from Pipeline Registration (R2, R4, R7)
and Provider Lifecycle (R8, R9).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bubus import EventBus

from tachikoma.per_message_pre_processing import (
    MessageContextProvider,
    MessagePreProcessingPipeline,
)
from tachikoma.plugins.events import PluginInstalled, PluginRemoving
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.provider_listeners import register_plugin_provider_listeners
from tachikoma.plugins.sources import LocalPluginSource
from tachikoma.pre_processing import ContextProvider, ContextResult, PreProcessingPipeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeSessionProvider(ContextProvider):
    async def provide(self, message: str) -> ContextResult | None:
        return ContextResult(tag="test", content="session data")

    def status_message(self, result: ContextResult | None = None) -> str:
        return "Session provider"


class _FakeMessageProvider(MessageContextProvider):
    async def provide(self, message, **kwargs):
        return [ContextResult(tag="test", content="message data")]

    def status_message(self, result=None) -> str:
        return "Message provider"


class _FakeDualProvider(ContextProvider, MessageContextProvider):
    async def provide(self, message, **kwargs):
        return ContextResult(tag="dual", content="dual data")

    def status_message(self, result=None) -> str:
        return "Dual provider"


def _make_plugin(
    alias: str,
    *,
    context_providers: list[ContextProvider] | None = None,
    message_context_providers: list[MessageContextProvider] | None = None,
) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=Path("/tmp/dummy")),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test plugin",
            source_format="tachikoma",
            skill_dirs=[],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
        context_providers=context_providers or [],
        message_context_providers=message_context_providers or [],
    )


def _make_mock_plugin_manager(plugins: dict[str, LoadedPlugin]) -> MagicMock:
    manager = MagicMock()
    manager.loaded_plugins.return_value = plugins
    return manager


async def _dispatch_and_cleanup(bus: EventBus, event: object) -> None:
    """Dispatch event, wait for handlers, then stop the bus."""
    await bus.dispatch(event)
    await bus.wait_until_idle()
    await bus.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestProviderListeners:
    """Provider listener registration and unregistration."""

    async def test_on_plugin_installed_registers_session_providers(
        self, tmp_path: Path
    ) -> None:
        """R2: PluginInstalled → session providers registered in PreProcessingPipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        provider = _FakeSessionProvider()
        plugin = _make_plugin("test", context_providers=[provider])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in pre_pipeline._providers

    async def test_on_plugin_installed_registers_message_providers(
        self, tmp_path: Path
    ) -> None:
        """R2: PluginInstalled → message providers registered in MessagePreProcessingPipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        provider = _FakeMessageProvider()
        plugin = _make_plugin("test", message_context_providers=[provider])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in msg_pre_pipeline._providers

    async def test_on_plugin_installed_dual_abc_registers_both(
        self, tmp_path: Path
    ) -> None:
        """R2: Dual-ABC provider registered in both pipelines."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        provider = _FakeDualProvider()
        plugin = _make_plugin(
            "test",
            context_providers=[provider],
            message_context_providers=[provider],
        )
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in pre_pipeline._providers
        assert provider in msg_pre_pipeline._providers

    async def test_on_plugin_removing_unregisters_all_providers(
        self, tmp_path: Path
    ) -> None:
        """R9: PluginRemoving → all providers unregistered from pipelines."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        session_provider = _FakeSessionProvider()
        msg_provider = _FakeMessageProvider()
        plugin = _make_plugin(
            "test",
            context_providers=[session_provider],
            message_context_providers=[msg_provider],
        )
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        # First register via install event
        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert session_provider in pre_pipeline._providers
        assert msg_provider in msg_pre_pipeline._providers

        # Now remove via removing event
        bus2 = EventBus()
        register_plugin_provider_listeners(bus2, pre_pipeline, msg_pre_pipeline, manager)
        await _dispatch_and_cleanup(
            bus2,
            PluginRemoving(alias="test", namespaced_skill_names=[]),
        )

        assert session_provider not in pre_pipeline._providers
        assert msg_provider not in msg_pre_pipeline._providers

    async def test_plugin_without_providers_is_noop(self, tmp_path: Path) -> None:
        """R2: Plugin with no providers — listener is a no-op."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        plugin = _make_plugin("test")
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert pre_pipeline._providers == []
        assert msg_pre_pipeline._providers == []

    async def test_on_plugin_removing_unknown_alias_is_noop(self, tmp_path: Path) -> None:
        """R9: PluginRemoving for unknown alias — no error, graceful handling."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        provider = _FakeSessionProvider()

        # Register a provider first
        pre_pipeline.register(provider)

        manager = _make_mock_plugin_manager({})  # No plugins loaded

        register_plugin_provider_listeners(bus, pre_pipeline, msg_pre_pipeline, manager)

        await _dispatch_and_cleanup(
            bus,
            PluginRemoving(alias="nonexistent", namespaced_skill_names=[]),
        )

        # Provider remains registered since plugin wasn't found
        assert provider in pre_pipeline._providers


class TestEventDispatchOrdering:
    """Verify event dispatch ordering for provider lifecycle."""

    async def test_removing_then_installed_for_update(self, tmp_path: Path) -> None:
        """R8: Update flow — Removing unregisters, Installed registers new."""
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()

        old_provider = _FakeSessionProvider()
        new_provider = _FakeSessionProvider()

        old_plugin = _make_plugin("test", context_providers=[old_provider])
        new_plugin = _make_plugin("test", context_providers=[new_provider])

        # Register old provider
        pre_pipeline.register(old_provider)
        assert old_provider in pre_pipeline._providers

        # Simulate update: Removing → Installed
        bus1 = EventBus()
        manager_old = _make_mock_plugin_manager({"test": old_plugin})
        register_plugin_provider_listeners(bus1, pre_pipeline, msg_pre_pipeline, manager_old)
        await _dispatch_and_cleanup(
            bus1,
            PluginRemoving(alias="test", namespaced_skill_names=[]),
        )

        assert old_provider not in pre_pipeline._providers

        bus2 = EventBus()
        manager_new = _make_mock_plugin_manager({"test": new_plugin})
        register_plugin_provider_listeners(bus2, pre_pipeline, msg_pre_pipeline, manager_new)
        await _dispatch_and_cleanup(
            bus2,
            PluginInstalled(alias="test", plugin=new_plugin),
        )

        assert new_provider in pre_pipeline._providers
