"""Tests for plugin lifecycle: init hook execution, event subscription, and error isolation.

Covers AC from the "Init Hook Execution", "Event Subscription", "Error Isolation",
and "Timeout" blocks of the spec.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from bubus import EventBus

from tachikoma.buffer.events import CoordinatorIdle
from tachikoma.plugins.context import PluginContext
from tachikoma.plugins.events import PluginInstalled
from tachikoma.plugins.lifecycle import (
    run_plugin_init_hooks,
    subscribe_plugin_events,
    unsubscribe_plugin_events,
)
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import LocalPluginSource

_NOW = datetime.now(UTC)


def _idle_event() -> CoordinatorIdle:
    return CoordinatorIdle(timestamp=_NOW)


def _make_plugin_with_hook(
    alias: str,
    init_hook: object | None = None,
    event_handlers: dict | None = None,
) -> LoadedPlugin:
    """Create a LoadedPlugin with init_hook and/or event_handlers."""
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=Path("/tmp/dummy")),
        manifest=PluginManifest(
            name=alias,
            version="1.0.0",
            description="Test",
            source_format="tachikoma",
            skill_dirs=[],
        ),
        status="loaded",
        diagnostic=None,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
        config={"key": "value"},
        init_hook=init_hook,
        event_handlers=event_handlers or {},
    )


@pytest.fixture()
async def bus():
    """Provide a fresh EventBus that is stopped after each test."""
    b = EventBus()
    yield b
    await b.stop()


class TestInitHookExecution:
    """Init hooks run before coordinator, in alphabetical order."""

    async def test_init_hook_receives_plugin_context(self, bus: EventBus) -> None:
        """AC: Init hook receives PluginContext with correct fields."""
        received: list[PluginContext] = []

        def hook(ctx: PluginContext) -> None:
            received.append(ctx)

        plugin = _make_plugin_with_hook("alpha", init_hook=hook)
        await run_plugin_init_hooks([plugin], bus)

        assert len(received) == 1
        ctx = received[0]
        assert ctx.alias == "alpha"
        assert ctx.config == {"key": "value"}
        assert ctx.event_bus is bus
        assert ctx.install_path == Path("/tmp/plugins/alpha")

    async def test_multiple_init_hooks_alphabetical(self, bus: EventBus) -> None:
        """AC: Init hooks run in alphabetical order by alias."""
        order: list[str] = []

        def make_hook(name: str):
            def hook(ctx: PluginContext) -> None:
                order.append(name)
            return hook

        p1 = _make_plugin_with_hook("zeta", init_hook=make_hook("zeta"))
        p2 = _make_plugin_with_hook("alpha", init_hook=make_hook("alpha"))
        p3 = _make_plugin_with_hook("mid", init_hook=make_hook("mid"))

        await run_plugin_init_hooks([p1, p2, p3], bus)

        assert order == ["alpha", "mid", "zeta"]

    async def test_successful_init_enables_event_subscription(self, bus: EventBus) -> None:
        """AC: After successful init, event handlers are subscribed."""
        events_received: list[object] = []

        def hook(ctx: PluginContext) -> None:
            pass

        def handler(event: object, ctx: PluginContext) -> None:
            events_received.append(event)

        plugin = _make_plugin_with_hook(
            "alpha",
            init_hook=hook,
            event_handlers={CoordinatorIdle: handler},
        )
        await run_plugin_init_hooks([plugin], bus)

        # Dispatch event and verify handler receives it
        await bus.dispatch(_idle_event())
        assert len(events_received) == 1

    async def test_async_init_hook(self, bus: EventBus) -> None:
        """AC: Async init hooks are awaited."""
        received: list[PluginContext] = []

        async def hook(ctx: PluginContext) -> None:
            await asyncio.sleep(0)
            received.append(ctx)

        plugin = _make_plugin_with_hook("alpha", init_hook=hook)
        await run_plugin_init_hooks([plugin], bus)

        assert len(received) == 1

    async def test_no_init_hook_but_events_subscribed_immediately(self, bus: EventBus) -> None:
        """AC: Plugins without init hooks get events subscribed immediately."""
        events_received: list[object] = []

        def handler(event: object, ctx: PluginContext) -> None:
            events_received.append(event)

        plugin = _make_plugin_with_hook(
            "alpha",
            event_handlers={CoordinatorIdle: handler},
        )
        await run_plugin_init_hooks([plugin], bus)

        await bus.dispatch(_idle_event())
        assert len(events_received) == 1

    async def test_init_hook_but_no_events(self, bus: EventBus) -> None:
        """AC: Init hook runs even when no events declared."""
        called = []

        def hook(ctx: PluginContext) -> None:
            called.append(True)

        plugin = _make_plugin_with_hook("alpha", init_hook=hook)
        await run_plugin_init_hooks([plugin], bus)

        assert called == [True]
        assert len(plugin.event_wrappers) == 0


class TestErrorIsolation:
    """Init hook and event handler failures are isolated per-plugin."""

    async def test_init_hook_failure_isolated(self, bus: EventBus) -> None:
        """AC: Plugin A init fails, plugin B proceeds normally."""
        b_called = []

        def hook_a(ctx: PluginContext) -> None:
            raise RuntimeError("boom")

        def hook_b(ctx: PluginContext) -> None:
            b_called.append(True)

        pa = _make_plugin_with_hook("alpha", init_hook=hook_a)
        pb = _make_plugin_with_hook("beta", init_hook=hook_b)
        await run_plugin_init_hooks([pa, pb], bus)

        assert b_called == [True]

    async def test_failed_init_no_event_subscription(self, bus: EventBus) -> None:
        """AC: Failed init means event subscriptions not registered."""
        events_received: list[object] = []

        def hook(ctx: PluginContext) -> None:
            raise RuntimeError("boom")

        def handler(event: object, ctx: PluginContext) -> None:
            events_received.append(event)

        plugin = _make_plugin_with_hook(
            "alpha",
            init_hook=hook,
            event_handlers={CoordinatorIdle: handler},
        )
        await run_plugin_init_hooks([plugin], bus)

        await bus.dispatch(_idle_event())
        assert len(events_received) == 0

    async def test_event_handler_failure_isolated(self, bus: EventBus) -> None:
        """AC: Handler A raises, handler B still called."""
        b_received = []

        def handler_a(event: object, ctx: PluginContext) -> None:
            raise RuntimeError("boom")

        def handler_b(event: object, ctx: PluginContext) -> None:
            b_received.append(event)

        pa = _make_plugin_with_hook(
            "alpha", event_handlers={CoordinatorIdle: handler_a}
        )
        pb = _make_plugin_with_hook(
            "beta", event_handlers={CoordinatorIdle: handler_b}
        )
        await run_plugin_init_hooks([pa, pb], bus)

        await bus.dispatch(_idle_event())
        assert len(b_received) == 1

    async def test_handler_return_value_ignored(self, bus: EventBus) -> None:
        """AC: Handler return values are ignored."""
        def handler(event: object, ctx: PluginContext) -> str:
            return "should be ignored"

        plugin = _make_plugin_with_hook(
            "alpha", event_handlers={CoordinatorIdle: handler}
        )
        await run_plugin_init_hooks([plugin], bus)

        # Should not raise
        await bus.dispatch(_idle_event())


class TestTimeout:
    """Init hooks have a 30-second timeout."""

    async def test_init_hook_timeout(self) -> None:
        """AC: Init hook exceeding 30s is cancelled and logged."""
        async def slow_hook(ctx: PluginContext) -> None:
            await asyncio.sleep(60)

        plugin = _make_plugin_with_hook("alpha", init_hook=slow_hook)
        bus = EventBus()
        # The lifecycle code applies a 30s timeout internally.
        # Use asyncio.timeout as a safety net to verify it completes.
        async with asyncio.timeout(35):
            await run_plugin_init_hooks([plugin], bus)
        # No event wrappers registered
        assert len(plugin.event_wrappers) == 0
        await bus.stop()


class TestUnsubscription:
    """Event wrapper deactivation on plugin removal."""

    async def test_unsubscribe_deactivates_wrappers(self, bus: EventBus) -> None:
        """AC: Unsubscribe makes wrappers no-ops."""
        events_received: list[object] = []

        def handler(event: object, ctx: PluginContext) -> None:
            events_received.append(event)

        plugin = _make_plugin_with_hook(
            "alpha", event_handlers={CoordinatorIdle: handler}
        )
        ctx = PluginContext(
            config={}, event_bus=bus, alias="alpha",
            install_path=Path("/tmp/alpha"),
        )
        subscribe_plugin_events(plugin, bus, ctx)

        # Before unsubscribe: handler works
        await bus.dispatch(_idle_event())
        assert len(events_received) == 1

        # After unsubscribe: handler deactivated
        unsubscribe_plugin_events(plugin.event_wrappers)
        await bus.dispatch(_idle_event())
        assert len(events_received) == 1  # No new event received

    async def test_multiple_event_types_routed(self, bus: EventBus) -> None:
        """AC: Multiple event types routed to correct handlers."""
        idle_received = []
        installed_received = []

        def on_idle(event: object, ctx: PluginContext) -> None:
            idle_received.append(event)

        def on_installed(event: object, ctx: PluginContext) -> None:
            installed_received.append(event)

        plugin = _make_plugin_with_hook(
            "alpha",
            event_handlers={
                CoordinatorIdle: on_idle,
                PluginInstalled: on_installed,
            },
        )
        await run_plugin_init_hooks([plugin], bus)

        await bus.dispatch(_idle_event())
        assert len(idle_received) == 1
        assert len(installed_received) == 0

        await bus.dispatch(PluginInstalled(alias="test", plugin=None))
        assert len(installed_received) == 1
