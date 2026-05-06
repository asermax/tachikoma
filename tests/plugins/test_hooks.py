"""Tests for the plugins bootstrap hook.

Covers: fresh workspace setup, plugin loading, gitignore idempotency,
and bus/database pre-condition enforcement.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus

from tachikoma.plugins.hooks import plugins_hook
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.sources import LocalPluginSource


def _make_ctx(
    workspace_path: Path,
    bus: EventBus | None = None,
    plugins: dict | None = None,
    database: object | None = MagicMock(),
):
    ctx = MagicMock()
    ctx.settings_manager = MagicMock()
    ctx.settings_manager.settings = MagicMock()
    ctx.settings_manager.settings.workspace = MagicMock()
    ctx.settings_manager.settings.workspace.path = workspace_path
    ctx.settings_manager.settings.plugins = plugins or {}
    ctx.extras = {}
    if database is not None:
        ctx.extras["database"] = database
    if bus is not None:
        ctx.extras["event_bus"] = bus
    return ctx


def _make_loaded_plugin(alias: str) -> LoadedPlugin:
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
    )


class TestPluginsHook:
    async def test_fresh_workspace_empty_plugins(self, tmp_path: Path) -> None:
        """Fresh workspace + empty [plugins] → directory created, gitignore line, empty manager."""
        bus = EventBus()
        ctx = _make_ctx(tmp_path, bus=bus)

        with patch("tachikoma.plugins.hooks.reconcile", new_callable=AsyncMock) as mock_reconcile, \
             patch("tachikoma.plugins.hooks.discover") as mock_discover:
            mock_reconcile.return_value = MagicMock(errors=[], any_errors=False)
            mock_discover.return_value = []

            await plugins_hook(ctx)

        # Directories created.
        assert (tmp_path / ".tachikoma" / "plugins").is_dir()
        assert (tmp_path / ".tachikoma" / "plugins" / ".staging").is_dir()

        # Gitignore entry appended.
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert ".tachikoma/plugins/" in gitignore.read_text()

        # Manager in extras with no loaded plugins.
        manager = ctx.extras["plugin_manager"]
        assert manager.list_plugins() == []

        # state_repo populated in extras.
        assert "state_repo" in ctx.extras

        await bus.stop()

    async def test_gitignore_idempotent(self, tmp_path: Path) -> None:
        """Running the hook twice does not duplicate .gitignore lines."""
        bus = EventBus()
        ctx = _make_ctx(tmp_path, bus=bus)

        with patch("tachikoma.plugins.hooks.reconcile", new_callable=AsyncMock) as mock_reconcile, \
             patch("tachikoma.plugins.hooks.discover") as mock_discover:
            mock_reconcile.return_value = MagicMock(errors=[], any_errors=False)
            mock_discover.return_value = []

            await plugins_hook(ctx)
            await plugins_hook(ctx)

        gitignore = (tmp_path / ".gitignore").read_text()
        assert gitignore.count(".tachikoma/plugins/") == 1

        await bus.stop()

    async def test_plugin_skill_paths_populated(self, tmp_path: Path) -> None:
        """Loaded plugin with skill_dirs → plugin_skill_paths populated."""
        bus = EventBus()
        ctx = _make_ctx(tmp_path, bus=bus)

        skill_dir = tmp_path / "my_skills"
        skill_dir.mkdir()
        plugin = _make_loaded_plugin("test-plugin")
        # Override manifest to include skill_dirs.
        object.__setattr__(
            plugin,
            "manifest",
            PluginManifest(
                name="test-plugin",
                version="1.0.0",
                description="Test",
                source_format="tachikoma",
                skill_dirs=[skill_dir],
            ),
        )

        with patch("tachikoma.plugins.hooks.reconcile", new_callable=AsyncMock) as mock_reconcile, \
             patch("tachikoma.plugins.hooks.discover") as mock_discover:
            mock_reconcile.return_value = MagicMock(errors=[], any_errors=False)
            mock_discover.return_value = [plugin]

            await plugins_hook(ctx)

        paths = ctx.extras["plugin_skill_paths"]
        assert len(paths) == 1
        assert paths[0] == ("test-plugin", skill_dir)

        await bus.stop()

    async def test_database_precondition_raises(self, tmp_path: Path) -> None:
        """Missing database in extras → RuntimeError with clear message."""
        ctx = _make_ctx(tmp_path, bus=EventBus(), database=None)

        with (
            patch("tachikoma.plugins.hooks.reconcile", new_callable=AsyncMock),
            patch("tachikoma.plugins.hooks.discover"),
            pytest.raises(RuntimeError, match="database"),
        ):
            await plugins_hook(ctx)

    async def test_bus_precondition_raises(self, tmp_path: Path) -> None:
        """Missing event_bus in extras → RuntimeError with clear message."""
        ctx = _make_ctx(tmp_path, bus=None)  # No bus.

        with patch("tachikoma.plugins.hooks.reconcile", new_callable=AsyncMock) as mock_reconcile, \
             patch("tachikoma.plugins.hooks.discover") as mock_discover:
            mock_reconcile.return_value = MagicMock(errors=[], any_errors=False)
            mock_discover.return_value = []

            with pytest.raises(RuntimeError, match="event_bus"):
                await plugins_hook(ctx)
