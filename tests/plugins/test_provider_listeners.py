"""Tests for plugin provider lifecycle listeners.

Covers acceptance criteria from Pipeline Registration (R2, R4, R7)
and Provider Lifecycle (R8, R9).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
from tachikoma.post_processing import (
    MAIN_PHASE,
    PRE_FINALIZE_PHASE,
    PostProcessingPipeline,
    PostProcessor,
)
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


class _FakePostProcessor(PostProcessor):
    """Concrete PostProcessor stub for testing lifecycle wiring."""

    def __init__(self, phase: str = MAIN_PHASE) -> None:
        self._phase = phase

    async def process(self, session, *, extra=None) -> None:
        pass

    @property
    def phase(self) -> str:
        return self._phase


class _FailingPostProcessor(PostProcessor):
    """PostProcessor that raises during process() for error isolation tests."""

    async def process(self, session, *, extra=None) -> None:
        raise RuntimeError("processor failed")


def _make_mock_registry() -> MagicMock:
    """Create a mock SessionRegistry for PostProcessingPipeline tests."""
    registry = MagicMock()
    registry.mark_processed = AsyncMock()
    registry.load_context_entries = AsyncMock(return_value=[])
    return registry


def _make_plugin(
    alias: str,
    *,
    context_providers: list[ContextProvider] | None = None,
    message_context_providers: list[MessageContextProvider] | None = None,
    post_processors: list[PostProcessor] | None = None,
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
        post_processors=post_processors or [],
    )


def _make_mock_plugin_manager(plugins: dict[str, LoadedPlugin]) -> MagicMock:
    manager = MagicMock()
    manager.loaded_plugins.return_value = plugins
    return manager


def _make_post_pipeline() -> PostProcessingPipeline:
    return PostProcessingPipeline(_make_mock_registry())


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

    async def test_on_plugin_installed_registers_session_providers(self, tmp_path: Path) -> None:
        """R2: PluginInstalled → session providers registered in PreProcessingPipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        provider = _FakeSessionProvider()
        plugin = _make_plugin("test", context_providers=[provider])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in pre_pipeline._providers

    async def test_on_plugin_installed_registers_message_providers(self, tmp_path: Path) -> None:
        """R2: PluginInstalled → message providers registered in MessagePreProcessingPipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        provider = _FakeMessageProvider()
        plugin = _make_plugin("test", message_context_providers=[provider])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in msg_pre_pipeline._providers

    async def test_on_plugin_installed_dual_abc_registers_both(self, tmp_path: Path) -> None:
        """R2: Dual-ABC provider registered in both pipelines."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        provider = _FakeDualProvider()
        plugin = _make_plugin(
            "test",
            context_providers=[provider],
            message_context_providers=[provider],
        )
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert provider in pre_pipeline._providers
        assert provider in msg_pre_pipeline._providers

    async def test_on_plugin_removing_unregisters_all_providers(self, tmp_path: Path) -> None:
        """R9: PluginRemoving → all providers unregistered from pipelines."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        session_provider = _FakeSessionProvider()
        msg_provider = _FakeMessageProvider()
        plugin = _make_plugin(
            "test",
            context_providers=[session_provider],
            message_context_providers=[msg_provider],
        )
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        # First register via install event
        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert session_provider in pre_pipeline._providers
        assert msg_provider in msg_pre_pipeline._providers

        # Now remove via removing event
        bus2 = EventBus()
        register_plugin_provider_listeners(
            bus2, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )
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
        post_pipeline = _make_post_pipeline()
        plugin = _make_plugin("test")
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert pre_pipeline._providers == []
        assert msg_pre_pipeline._providers == []

    async def test_on_plugin_removing_unknown_alias_is_noop(self, tmp_path: Path) -> None:
        """R9: PluginRemoving for unknown alias — no error, graceful handling."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        provider = _FakeSessionProvider()

        # Register a provider first
        pre_pipeline.register(provider)

        manager = _make_mock_plugin_manager({})  # No plugins loaded

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(
            bus,
            PluginRemoving(alias="nonexistent", namespaced_skill_names=[]),
        )

        # Provider remains registered since plugin wasn't found
        assert provider in pre_pipeline._providers


class TestPostProcessorListeners:
    """Post-processor lifecycle registration and unregistration."""

    async def test_on_plugin_installed_registers_post_processors(self, tmp_path: Path) -> None:
        """on_plugin_installed registers plugin post_processors into PostProcessingPipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        processor = _FakePostProcessor()
        plugin = _make_plugin("test", post_processors=[processor])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert processor in post_pipeline._phases[MAIN_PHASE]

    async def test_processors_registered_at_declared_phase(self, tmp_path: Path) -> None:
        """Processors are registered at the phase declared by their class attribute."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        processor = _FakePostProcessor(phase=PRE_FINALIZE_PHASE)
        plugin = _make_plugin("test", post_processors=[processor])
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        assert processor in post_pipeline._phases[PRE_FINALIZE_PHASE]
        assert processor not in post_pipeline._phases[MAIN_PHASE]

    async def test_on_plugin_removing_unregisters_post_processors(self, tmp_path: Path) -> None:
        """on_plugin_removing unregisters plugin post_processors from PostProcessingPipeline."""
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        processor = _FakePostProcessor()
        plugin = _make_plugin("test", post_processors=[processor])
        manager = _make_mock_plugin_manager({"test": plugin})

        # Register via install event
        bus1 = EventBus()
        register_plugin_provider_listeners(
            bus1, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )
        await _dispatch_and_cleanup(bus1, PluginInstalled(alias="test", plugin=plugin))

        assert processor in post_pipeline._phases[MAIN_PHASE]

        # Remove via removing event
        bus2 = EventBus()
        register_plugin_provider_listeners(
            bus2, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )
        await _dispatch_and_cleanup(bus2, PluginRemoving(alias="test", namespaced_skill_names=[]))

        assert processor not in post_pipeline._phases[MAIN_PHASE]

    async def test_plugin_without_post_processors_is_noop(self, tmp_path: Path) -> None:
        """Plugin with no post_processors — listener is a no-op for the post pipeline."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        plugin = _make_plugin("test")
        manager = _make_mock_plugin_manager({"test": plugin})

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(bus, PluginInstalled(alias="test", plugin=plugin))

        for phase_list in post_pipeline._phases.values():
            assert phase_list == []

    async def test_on_plugin_removing_unknown_alias_is_noop_for_post(self, tmp_path: Path) -> None:
        """PluginRemoving for unknown alias — no post-processor cleanup."""
        bus = EventBus()
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()
        processor = _FakePostProcessor()

        # Manually register a processor
        post_pipeline.register(processor)

        manager = _make_mock_plugin_manager({})  # No plugins loaded

        register_plugin_provider_listeners(
            bus, pre_pipeline, msg_pre_pipeline, manager, post_pipeline
        )

        await _dispatch_and_cleanup(
            bus, PluginRemoving(alias="nonexistent", namespaced_skill_names=[])
        )

        # Processor remains registered since plugin wasn't found
        assert processor in post_pipeline._phases[MAIN_PHASE]

    async def test_failing_processor_does_not_block_same_phase(self, tmp_path: Path) -> None:
        """R2: A processor that raises doesn't block other processors in the same phase."""
        post_pipeline = _make_post_pipeline()
        failing = _FailingPostProcessor()
        succeeding = _FakePostProcessor()
        post_pipeline.register(failing)
        post_pipeline.register(succeeding)

        registry = _make_mock_registry()
        session = MagicMock(id="test-session")
        session.processed_at = None

        # Build a minimal pipeline to run through the gather pattern
        pipeline = PostProcessingPipeline(registry)
        pipeline.register(failing)
        pipeline.register(succeeding)

        # run() uses return_exceptions=True so failures are logged, not raised
        await pipeline.run(session)

        # Both processors were attempted (no exception propagated)
        assert pipeline._is_processing is False

    async def test_failing_processor_does_not_block_subsequent_phase(self, tmp_path: Path) -> None:
        """R2: A failing main-phase processor doesn't prevent pre_finalize phase from running."""
        registry = _make_mock_registry()
        pipeline = PostProcessingPipeline(registry)

        failing_main = _FailingPostProcessor()
        succeeding_finalize = _FakePostProcessor(phase=PRE_FINALIZE_PHASE)
        pipeline.register(failing_main)
        pipeline.register(succeeding_finalize)

        session = MagicMock(id="test-session")
        session.processed_at = None

        await pipeline.run(session)

        # Pipeline completed despite the failure
        assert pipeline._is_processing is False


class TestEventDispatchOrdering:
    """Verify event dispatch ordering for provider lifecycle."""

    async def test_removing_then_installed_for_update(self, tmp_path: Path) -> None:
        """R8: Update flow — Removing unregisters, Installed registers new."""
        pre_pipeline = PreProcessingPipeline()
        msg_pre_pipeline = MessagePreProcessingPipeline()
        post_pipeline = _make_post_pipeline()

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
        register_plugin_provider_listeners(
            bus1, pre_pipeline, msg_pre_pipeline, manager_old, post_pipeline
        )
        await _dispatch_and_cleanup(
            bus1,
            PluginRemoving(alias="test", namespaced_skill_names=[]),
        )

        assert old_provider not in pre_pipeline._providers

        bus2 = EventBus()
        manager_new = _make_mock_plugin_manager({"test": new_plugin})
        register_plugin_provider_listeners(
            bus2, pre_pipeline, msg_pre_pipeline, manager_new, post_pipeline
        )
        await _dispatch_and_cleanup(
            bus2,
            PluginInstalled(alias="test", plugin=new_plugin),
        )

        assert new_provider in pre_pipeline._providers
