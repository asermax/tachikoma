"""Tests for plugin lifecycle events (DLT-048 Batch 5, Step 5.1).

Verifies that plugin events can be dispatched and received via the bubus
EventBus with correct payloads.
"""

from pathlib import Path

from bubus import EventBus

from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.sources import LocalPluginSource


def _stub_plugin(alias: str) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=LocalPluginSource(path=Path("/tmp/dummy")),
        manifest=None,
        status="loaded",
        diagnostic=None,
        plugin_dir=Path(f"/tmp/plugins/{alias}"),
    )


class TestPluginEvents:
    """Smoke tests for event dispatch/subscribe round-trips."""

    async def test_plugin_installed_dispatch(self) -> None:
        bus = EventBus()
        received: list[PluginInstalled] = []

        async def handler(event: PluginInstalled) -> None:
            received.append(event)

        bus.on(PluginInstalled, handler)

        plugin = _stub_plugin("test-plugin")
        event = PluginInstalled(alias="test-plugin", plugin=plugin)
        await bus.dispatch(event)
        await bus.wait_until_idle()
        await bus.stop()

        assert len(received) == 1
        assert received[0].alias == "test-plugin"
        assert received[0].plugin.alias == "test-plugin"

    async def test_plugin_removing_dispatch(self) -> None:
        bus = EventBus()
        received: list[PluginRemoving] = []

        async def handler(event: PluginRemoving) -> None:
            received.append(event)

        bus.on(PluginRemoving, handler)

        event = PluginRemoving(
            alias="my-plugin",
            namespaced_skill_names=["my-plugin:linter", "my-plugin:deploy"],
        )
        await bus.dispatch(event)
        await bus.wait_until_idle()
        await bus.stop()

        assert len(received) == 1
        assert received[0].alias == "my-plugin"
        assert received[0].namespaced_skill_names == [
            "my-plugin:linter",
            "my-plugin:deploy",
        ]

    async def test_plugin_removed_dispatch(self) -> None:
        bus = EventBus()
        received: list[PluginRemoved] = []

        async def handler(event: PluginRemoved) -> None:
            received.append(event)

        bus.on(PluginRemoved, handler)

        event = PluginRemoved(alias="gone-plugin")
        await bus.dispatch(event)
        await bus.wait_until_idle()
        await bus.stop()

        assert len(received) == 1
        assert received[0].alias == "gone-plugin"
