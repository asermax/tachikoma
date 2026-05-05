"""Tests for PluginManager.

Covers install/remove/list flows, alias resolution, collision errors,
concurrency serialization, and failure isolation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus

from tachikoma.plugins.events import PluginRemoved, PluginRemoving
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manager import (
    PluginAliasCollisionError,
    PluginInstallError,
    PluginManager,
    PluginNotFoundError,
)
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.materializer import MaterializeError
from tachikoma.plugins.sources import LocalPluginSource

from .conftest import make_plugin as _make_plugin

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings_manager() -> MagicMock:
    sm = MagicMock()
    sm.settings = MagicMock()
    sm.settings.plugins = {}
    sm.update_plugin_entry = MagicMock()
    sm.remove_plugin_entry = MagicMock()
    return sm


def _make_manager(
    *,
    workspace: Path,
    loaded: dict[str, LoadedPlugin] | None = None,
    bus: EventBus | None = None,
) -> PluginManager:
    sm = _make_settings_manager()
    return PluginManager(
        settings_manager=sm,
        bus=bus or EventBus(),
        workspace_path=workspace,
        loaded=loaded or {},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestList:
    """AC-MCP-LIST-2: empty list when no plugins installed."""

    def test_list_empty(self, tmp_path: Path) -> None:
        mgr = _make_manager(workspace=tmp_path)
        assert mgr.list_plugins() == []

    def test_list_returns_loaded(self, tmp_path: Path) -> None:
        p = _make_plugin("alpha")
        mgr = _make_manager(workspace=tmp_path, loaded={"alpha": p})
        result = mgr.list_plugins()
        assert len(result) == 1
        assert result[0].alias == "alpha"


class TestFailedPlugins:
    """failed_plugins() returns plugins with status != 'loaded'."""

    def test_all_loaded_returns_empty(self, tmp_path: Path) -> None:
        p = _make_plugin("alpha")
        mgr = _make_manager(workspace=tmp_path, loaded={"alpha": p})
        assert mgr.failed_plugins() == []

    def test_one_failed_returns_it(self, tmp_path: Path) -> None:
        loaded = _make_plugin("good", status="loaded")
        failed = _make_plugin("bad", status="failed")
        mgr = _make_manager(
            workspace=tmp_path,
            loaded={"good": loaded, "bad": failed},
        )
        result = mgr.failed_plugins()
        assert len(result) == 1
        assert result[0].alias == "bad"
        assert result[0].status == "failed"

    def test_mixed_returns_only_failed(self, tmp_path: Path) -> None:
        p1 = _make_plugin("ok", status="loaded")
        p2 = _make_plugin("stale", status="stale-fallback")
        p3 = _make_plugin("bad", status="failed")
        mgr = _make_manager(
            workspace=tmp_path,
            loaded={"ok": p1, "stale": p2, "bad": p3},
        )
        result = mgr.failed_plugins()
        aliases = {p.alias for p in result}
        assert aliases == {"stale", "bad"}


class TestInstall:
    """AC-MCP-INST-1..6, INST-9."""

    async def test_install_validate_then_write_order(self, tmp_path: Path) -> None:
        """AC-MCP-INST-1, INST-2: validate-then-write — config only written on success."""
        mgr = _make_manager(workspace=tmp_path)
        source = LocalPluginSource(path=Path("/nonexistent/plugin"))

        with pytest.raises(PluginInstallError):
            await mgr.install(source)

        # Config was NOT mutated on failure.
        mgr._settings_manager.update_plugin_entry.assert_not_called()

    async def test_install_collision_from_manifest_name(self, tmp_path: Path) -> None:
        """AC-MCP-INST-3: collision with manifest.name and no explicit alias → retry hint."""
        existing = _make_plugin("linter")
        mgr = _make_manager(workspace=tmp_path, loaded={"linter": existing})

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
        ):
            mock_manifest = PluginManifest(
                name="linter",
                version=None,
                description="A linter",
                source_format="tachikoma",
                skill_dirs=[],
            )
            mock_parse.return_value = mock_manifest

            with pytest.raises(PluginAliasCollisionError) as exc_info:
                await mgr.install(
                    LocalPluginSource(path=Path("/some/plugin")),
                    alias=None,
                )

            assert exc_info.value.alias == "linter"
            assert exc_info.value.suggest_retry_with_alias is True

        # Config not mutated.
        mgr._settings_manager.update_plugin_entry.assert_not_called()

    async def test_install_explicit_alias_overrides_manifest(self, tmp_path: Path) -> None:
        """AC-MCP-INST-4: explicit alias overrides manifest.name."""
        mgr = _make_manager(workspace=tmp_path)

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
        ):
            mock_manifest = PluginManifest(
                name="original-name",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
            )
            mock_parse.return_value = mock_manifest

            result = await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="my-cr",
            )

            await mgr._bus.wait_until_idle()
            await mgr._bus.stop()

            assert result.alias == "my-cr"
            mgr._settings_manager.update_plugin_entry.assert_called_once()
            call_args = mgr._settings_manager.update_plugin_entry.call_args
            assert call_args[0][0] == "my-cr"

    async def test_install_explicit_alias_collision(self, tmp_path: Path) -> None:
        """AC-MCP-INST-5: explicit alias collision → error, no retry hint."""
        existing = _make_plugin("my-cr")
        mgr = _make_manager(workspace=tmp_path, loaded={"my-cr": existing})

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
        ):
            mock_parse.return_value = PluginManifest(
                name="anything",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
            )
            mgr._settings_manager.settings.plugins = {"my-cr": MagicMock()}

            with pytest.raises(PluginAliasCollisionError) as exc_info:
                await mgr.install(
                    LocalPluginSource(path=Path("/some/plugin")),
                    alias="my-cr",
                )

        assert exc_info.value.suggest_retry_with_alias is False

    async def test_install_failure_cleans_temp(self, tmp_path: Path) -> None:
        """AC-MCP-INST-6: failure → temp cleanup, config not mutated."""
        mgr = _make_manager(workspace=tmp_path)

        with patch(
            "tachikoma.plugins.manager.materialize_local",
            new_callable=AsyncMock,
            side_effect=MaterializeError("test", "/plugin", FileNotFoundError("boom")),
        ), pytest.raises(PluginInstallError):
            await mgr.install(LocalPluginSource(path=Path("/plugin")))

        mgr._settings_manager.update_plugin_entry.assert_not_called()

    async def test_concurrent_installs_serialized(self, tmp_path: Path) -> None:
        """AC-MCP-INST-9: two installs awaited together complete serialized."""
        mgr = _make_manager(workspace=tmp_path)

        call_order: list[str] = []

        async def slow_materialize(*args, **kwargs):
            call_order.append("start")
            await asyncio.sleep(0.05)
            call_order.append("end")

        with (
            patch(
                "tachikoma.plugins.manager.materialize_local",
                new_callable=AsyncMock,
                side_effect=slow_materialize,
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
        ):
            mock_parse.return_value = PluginManifest(
                name="alpha",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
            )

            # Run two installs concurrently.
            await asyncio.gather(
                mgr.install(LocalPluginSource(path=Path("/a")), alias="alpha"),
                mgr.install(LocalPluginSource(path=Path("/b")), alias="beta"),
                return_exceptions=True,
            )

        await mgr._bus.wait_until_idle()
        await mgr._bus.stop()

        # Both should succeed (or one might fail if beta also picks up same path).
        # The key assertion: materialize calls don't interleave.
        # Since both use the lock, the second starts only after the first ends.
        assert call_order == ["start", "end", "start", "end"]


class TestRemove:
    """AC-MCP-REM-1..4."""

    async def test_remove_not_found(self, tmp_path: Path) -> None:
        """AC-MCP-REM-2: removing a non-existent alias → error."""
        mgr = _make_manager(workspace=tmp_path)

        with pytest.raises(PluginNotFoundError) as exc_info:
            await mgr.remove("nonexistent")

        assert "nonexistent" in str(exc_info.value)

    async def test_remove_dispatches_events(self, tmp_path: Path) -> None:
        """AC-MCP-REM-1: PluginRemoving and PluginRemoved are dispatched."""
        bus = EventBus()
        removing_events: list = []
        removed_events: list = []

        async def on_removing(event: PluginRemoving) -> None:
            removing_events.append(event)

        async def on_removed(event: PluginRemoved) -> None:
            removed_events.append(event)

        bus.on(PluginRemoving, on_removing)
        bus.on(PluginRemoved, on_removed)

        plugin = _make_plugin("test-plugin")
        mgr = _make_manager(workspace=tmp_path, loaded={"test-plugin": plugin}, bus=bus)

        with patch("tachikoma.plugins.manager.shutil.rmtree"):
            result = await mgr.remove("test-plugin")

        await bus.wait_until_idle()
        await bus.stop()

        assert result is None  # No rmtree error
        assert len(removing_events) == 1
        assert removing_events[0].alias == "test-plugin"
        assert len(removed_events) == 1
        assert removed_events[0].alias == "test-plugin"

    async def test_remove_rmtree_failure_nonfatal(self, tmp_path: Path) -> None:
        """AC-MCP-REM-3: rmtree failure → config still removed, diagnostic returned."""
        plugin = _make_plugin("fragile")
        mgr = _make_manager(workspace=tmp_path, loaded={"fragile": plugin})

        # Create the expected install directory so target.exists() is True.
        plugin_install_dir = tmp_path / ".tachikoma" / "plugins" / "fragile"
        plugin_install_dir.mkdir(parents=True)

        with patch("tachikoma.plugins.manager.shutil.rmtree", side_effect=OSError("perm denied")):
            result = await mgr.remove("fragile")

        await mgr._bus.wait_until_idle()
        await mgr._bus.stop()

        assert result is not None
        assert "perm denied" in result
        # Config entry still removed.
        mgr._settings_manager.remove_plugin_entry.assert_called_once_with("fragile")
        # Plugin removed from loaded.
        assert mgr.list_plugins() == []

    async def test_remove_double_idempotent(self, tmp_path: Path) -> None:
        """AC-MCP-REM-4: second remove → not-found error."""
        plugin = _make_plugin("temp")
        mgr = _make_manager(workspace=tmp_path, loaded={"temp": plugin})

        with patch("tachikoma.plugins.manager.shutil.rmtree"):
            await mgr.remove("temp")

        await mgr._bus.wait_until_idle()
        await mgr._bus.stop()

        with pytest.raises(PluginNotFoundError):
            await mgr.remove("temp")
