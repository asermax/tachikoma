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

from tachikoma.plugins.events import PluginInstalled, PluginRemoved, PluginRemoving
from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.manager import (
    PluginAliasCollisionError,
    PluginInstallError,
    PluginManager,
    PluginNotFoundError,
)
from tachikoma.plugins.manifest import PluginManifest
from tachikoma.plugins.materializer import MaterializationResult, MaterializeError
from tachikoma.plugins.sources import GitPluginSource, LocalPluginSource, UrlPluginSource
from tachikoma.plugins.state import PluginState
from tachikoma.plugins.updater import UpdateResult

from .conftest import make_agent_defaults
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
    state_repo = MagicMock()
    return PluginManager(
        settings_manager=sm,
        bus=bus or EventBus(),
        workspace_path=workspace,
        loaded=loaded or {},
        state_repo=state_repo,
        agent_defaults=make_agent_defaults(workspace),
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

        with (
            patch(
                "tachikoma.plugins.manager.materialize_local",
                new_callable=AsyncMock,
                side_effect=MaterializeError("test", "/plugin", FileNotFoundError("boom")),
            ),
            pytest.raises(PluginInstallError),
        ):
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

    async def test_remove_deactivates_event_wrappers(self, tmp_path: Path) -> None:
        """AC: Runtime remove deactivates event wrappers."""
        plugin = _make_plugin("hooked")
        # Simulate active wrappers (normally set during lifecycle init)
        wrapper = MagicMock()
        plugin.event_wrappers.append(wrapper)

        mgr = _make_manager(workspace=tmp_path, loaded={"hooked": plugin})

        with patch("tachikoma.plugins.manager.shutil.rmtree"):
            await mgr.remove("hooked")

        await mgr._bus.wait_until_idle()
        await mgr._bus.stop()

        wrapper._deactivate.assert_called_once()


class TestRuntimeInstallWithHooks:
    """Runtime install with init hooks and event subscriptions."""

    async def test_install_with_init_hook_runs_before_installed_event(self, tmp_path: Path) -> None:
        """AC: Runtime install with init hook — hook runs before PluginInstalled."""
        bus = EventBus()
        call_order: list[str] = []

        async def on_installed(event: PluginInstalled) -> None:
            call_order.append("installed")

        bus.on(PluginInstalled, on_installed)

        mgr = _make_manager(workspace=tmp_path, bus=bus)

        fake_init = MagicMock()

        async def fake_init_plugin(plugin, bus):
            call_order.append("init_hook")

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager._validate_handlers",
                return_value=(fake_init, {}),
            ),
            patch("tachikoma.plugins.manager.init_plugin", side_effect=fake_init_plugin),
        ):
            mock_parse.return_value = PluginManifest(
                name="hooked",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
                hooks={"init": "init"},
                events={"coordinator_idle": "on_idle"},
            )

            result = await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="hooked",
            )
            assert result.alias == "hooked"

        await bus.wait_until_idle()
        await bus.stop()

        assert call_order == ["init_hook", "installed"]

    async def test_install_events_only_subscribed_immediately(self, tmp_path: Path) -> None:
        """AC: Runtime install without init hook but with events → init_plugin called."""
        bus = EventBus()
        mgr = _make_manager(workspace=tmp_path, bus=bus)

        fake_handler = MagicMock()

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager._validate_handlers",
                return_value=(None, {MagicMock(): fake_handler}),
            ),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_init,
        ):
            mock_parse.return_value = PluginManifest(
                name="events-only",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
                events={"coordinator_idle": "on_idle"},
            )

            await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="events-only",
            )
            mock_init.assert_called_once()

        await bus.wait_until_idle()
        await bus.stop()

    async def test_install_no_hooks_no_events_no_subscribe(self, tmp_path: Path) -> None:
        """AC: Runtime install with no hooks/events → init_plugin still called."""
        bus = EventBus()
        mgr = _make_manager(workspace=tmp_path, bus=bus)

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_init,
        ):
            mock_parse.return_value = PluginManifest(
                name="plain",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
            )

            await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="plain",
            )
            mock_init.assert_called_once()

        await bus.wait_until_idle()
        await bus.stop()

    async def test_install_failed_init_still_succeeds(self, tmp_path: Path) -> None:
        """AC: Runtime install with failed init → install succeeds, init_plugin returns False."""
        bus = EventBus()
        mgr = _make_manager(workspace=tmp_path, bus=bus)

        fake_init = MagicMock()

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager._validate_handlers",
                return_value=(fake_init, {}),
            ),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=False,
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name="failing",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
                hooks={"init": "init"},
                events={"coordinator_idle": "on_idle"},
            )

            result = await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="failing",
            )
            assert result.alias == "failing"

        await bus.wait_until_idle()
        await bus.stop()

    async def test_install_dispatches_plugin_installed(self, tmp_path: Path) -> None:
        """AC: Runtime install dispatches PluginInstalled event."""
        bus = EventBus()
        installed_events: list = []

        async def on_installed(event: PluginInstalled) -> None:
            installed_events.append(event)

        bus.on(PluginInstalled, on_installed)

        mgr = _make_manager(workspace=tmp_path, bus=bus)

        with (
            patch("tachikoma.plugins.manager.materialize_local", new_callable=AsyncMock),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
        ):
            mock_parse.return_value = PluginManifest(
                name="fresh",
                version=None,
                description="Test",
                source_format="tachikoma",
                skill_dirs=[],
            )

            result = await mgr.install(
                LocalPluginSource(path=Path("/some/plugin")),
                alias="fresh",
            )

        await bus.wait_until_idle()
        await bus.stop()

        assert result.alias == "fresh"
        assert len(installed_events) == 1
        assert installed_events[0].alias == "fresh"


# ---------------------------------------------------------------------------
# Update tests (Batch 4: Steps 8, 10)
# ---------------------------------------------------------------------------


def _make_git_plugin(alias: str) -> LoadedPlugin:
    """Create a git-source LoadedPlugin for testing updates."""
    return LoadedPlugin(
        alias=alias,
        source=GitPluginSource(git="https://github.com/test/plugin.git", ref="main"),
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


def _make_url_plugin(alias: str) -> LoadedPlugin:
    """Create a URL-source LoadedPlugin for testing updates."""
    return LoadedPlugin(
        alias=alias,
        source=UrlPluginSource(url="https://example.com/plugin.tar.gz"),
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


class TestUpdate:
    """Tests for PluginManager.update()."""

    async def test_update_not_found(self, tmp_path: Path) -> None:
        mgr = _make_manager(workspace=tmp_path)

        with pytest.raises(PluginNotFoundError):
            await mgr.update("nonexistent")

    async def test_update_local_returns_skipped(self, tmp_path: Path) -> None:
        plugin = _make_plugin("local-dev")
        mgr = _make_manager(workspace=tmp_path, loaded={"local-dev": plugin})

        result = await mgr.update("local-dev")

        assert result.status == "skipped"
        assert "always current" in (result.message or "")

    async def test_update_git_success(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_git_plugin("code-review")
        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": plugin}, bus=bus)

        mat_result = MaterializationResult(
            staging_dir=tmp_path / ".tachikoma" / "plugins" / ".staging" / "update-code-review",
            version="abc123def456",
        )

        state_repo = mgr._state_repo
        state_repo.get = AsyncMock(
            return_value=PluginState(
                alias="code-review",
                installed_version="old_sha",
                update_status="update-available",
                available_version="abc123def456",
                last_checked_at=None,
                diagnostic=None,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        state_repo.upsert = AsyncMock()

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=mat_result,
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name="code-review",
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )

            result = await mgr.update("code-review")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "updated"
        assert result.alias == "code-review"
        state_repo.upsert.assert_called()

    async def test_update_materialize_failure(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_git_plugin("code-review")
        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": plugin}, bus=bus)

        with patch(
            "tachikoma.plugins.manager.materialize_git",
            new_callable=AsyncMock,
            side_effect=MaterializeError("code-review", "git:test", RuntimeError("network")),
        ):
            result = await mgr.update("code-review")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "failed"
        assert "network" in result.error

    async def test_update_manifest_parse_failure(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_git_plugin("code-review")
        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": plugin}, bus=bus)

        staging = tmp_path / ".tachikoma" / "plugins" / ".staging" / "update-code-review"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version="new_sha"),
            ),
            patch(
                "tachikoma.plugins.manager.parse_manifest",
                side_effect=ValueError("bad manifest"),
            ),
        ):
            result = await mgr.update("code-review")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "failed"
        assert "Manifest parse" in result.error

    async def test_update_already_in_progress(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_git_plugin("code-review")
        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": plugin}, bus=bus)

        # Acquire the lock manually to simulate in-progress update.
        lock = mgr._get_update_lock("code-review")
        await lock.acquire()

        try:
            result = await mgr.update("code-review")
            assert result.status == "failed"
            assert "already in progress" in result.error
        finally:
            lock.release()

        await bus.wait_until_idle()
        await bus.stop()

    async def test_update_url_already_up_to_date(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_url_plugin("weather")
        mgr = _make_manager(workspace=tmp_path, loaded={"weather": plugin}, bus=bus)

        state_repo = mgr._state_repo
        state_repo.get = AsyncMock(
            return_value=PluginState(
                alias="weather",
                installed_version="existing_hash",
                update_status="up-to-date",
                available_version=None,
                last_checked_at=None,
                diagnostic=None,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )

        staging = tmp_path / ".tachikoma" / "plugins" / ".staging" / "update-weather"

        with (
            patch(
                "tachikoma.plugins.manager.materialize_url",
                new_callable=AsyncMock,
                return_value=MaterializationResult(staging_dir=staging, version="existing_hash"),
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
        ):
            mock_parse.return_value = PluginManifest(
                name="weather",
                version="1.0.0",
                description="Weather",
                source_format="tachikoma",
                skill_dirs=[],
            )

            result = await mgr.update("weather")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "skipped"
        assert "already up-to-date" in (result.message or "")


class TestUpdateAll:
    """Tests for PluginManager.update_all()."""

    async def test_update_all_mixed_results(self, tmp_path: Path) -> None:
        bus = EventBus()
        git_plugin = _make_git_plugin("code-review")
        local_plugin = _make_plugin("dev-tools")
        url_plugin = _make_url_plugin("weather")

        mgr = _make_manager(
            workspace=tmp_path,
            loaded={
                "code-review": git_plugin,
                "dev-tools": local_plugin,
                "weather": url_plugin,
            },
            bus=bus,
        )

        # code-review has update available, weather is up-to-date, dev-tools is local.
        state_repo = mgr._state_repo

        async def mock_get(alias):
            states = {
                "code-review": PluginState(
                    alias="code-review",
                    installed_version="old",
                    update_status="update-available",
                    available_version="new",
                    last_checked_at=None,
                    diagnostic=None,
                    created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                ),
                "weather": PluginState(
                    alias="weather",
                    installed_version="hash1",
                    update_status="up-to-date",
                    available_version=None,
                    last_checked_at=None,
                    diagnostic=None,
                    created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                ),
            }
            return states.get(alias)

        state_repo.get = mock_get

        # Mock the actual update for code-review.
        with patch.object(
            mgr,
            "update",
            new_callable=AsyncMock,
            return_value=UpdateResult(alias="code-review", status="updated"),
        ):
            summary = await mgr.update_all()

        await bus.wait_until_idle()
        await bus.stop()

        assert summary.total == 3
        assert summary.skipped == 2  # dev-tools (local) + weather (up-to-date)
        assert summary.updated == 1
        assert summary.failed == 0

    async def test_update_all_empty(self, tmp_path: Path) -> None:
        bus = EventBus()
        mgr = _make_manager(workspace=tmp_path, bus=bus)

        summary = await mgr.update_all()

        await bus.wait_until_idle()
        await bus.stop()

        assert summary.total == 0
        assert summary.results == []


class TestReregisterPlugin:
    """Tests for the re-registration flow within update()."""

    async def test_reregister_success_dispatches_lifecycle_events(self, tmp_path: Path) -> None:
        bus = EventBus()
        events_dispatched: list[str] = []

        async def on_removing(event):
            events_dispatched.append("removing")

        async def on_removed(event):
            events_dispatched.append("removed")

        async def on_installed(event):
            events_dispatched.append("installed")

        bus.on(PluginRemoving, on_removing)
        bus.on(PluginRemoved, on_removed)
        bus.on(PluginInstalled, on_installed)

        old_plugin = _make_git_plugin("code-review")
        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": old_plugin}, bus=bus)

        mat_result = MaterializationResult(
            staging_dir=tmp_path / ".tachikoma" / "plugins" / ".staging" / "update-code-review",
            version="new_sha",
        )

        state_repo = mgr._state_repo
        state_repo.get = AsyncMock(
            return_value=PluginState(
                alias="code-review",
                installed_version="old_sha",
                update_status="update-available",
                available_version="new_sha",
                last_checked_at=None,
                diagnostic=None,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        state_repo.upsert = AsyncMock()

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=mat_result,
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name="code-review",
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )

            result = await mgr.update("code-review")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "updated"
        assert "removing" in events_dispatched
        assert "removed" in events_dispatched
        assert "installed" in events_dispatched

    async def test_reregister_failure_retains_old_plugin(self, tmp_path: Path) -> None:
        bus = EventBus()
        old_plugin = _make_git_plugin("code-review")
        # Give the old plugin a contributed skill.
        mock_skill = MagicMock()
        mock_skill.qualified_name = "code-review:my-skill"
        old_plugin.contributed_skills.append(mock_skill)

        mgr = _make_manager(workspace=tmp_path, loaded={"code-review": old_plugin}, bus=bus)

        mat_result = MaterializationResult(
            staging_dir=tmp_path / ".tachikoma" / "plugins" / ".staging" / "update-code-review",
            version="new_sha",
        )

        state_repo = mgr._state_repo
        state_repo.get = AsyncMock(
            return_value=PluginState(
                alias="code-review",
                installed_version="old_sha",
                update_status="update-available",
                available_version="new_sha",
                last_checked_at=None,
                diagnostic=None,
                created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            )
        )
        state_repo.upsert = AsyncMock()

        with (
            patch(
                "tachikoma.plugins.manager.materialize_git",
                new_callable=AsyncMock,
                return_value=mat_result,
            ),
            patch("tachikoma.plugins.manager.parse_manifest") as mock_parse,
            patch("tachikoma.plugins.manager._atomic_replace_dir"),
            patch(
                "tachikoma.plugins.manager.init_plugin",
                new_callable=AsyncMock,
                side_effect=RuntimeError("skill name collision"),
            ),
        ):
            mock_parse.return_value = PluginManifest(
                name="code-review",
                version="2.0.0",
                description="Updated",
                source_format="tachikoma",
                skill_dirs=[],
            )

            result = await mgr.update("code-review")

        await bus.wait_until_idle()
        await bus.stop()

        assert result.status == "failed"
        assert "Re-registration failed" in result.error
        # Old plugin is still in loaded map.
        assert mgr._loaded["code-review"] is old_plugin
        # Diagnostic was stored.
        state_repo.upsert.assert_called()


class TestCleanupUpdateLock:
    """Tests for lock cleanup on plugin removal."""

    async def test_remove_cleans_up_update_lock(self, tmp_path: Path) -> None:
        bus = EventBus()
        plugin = _make_plugin("temp")
        mgr = _make_manager(workspace=tmp_path, loaded={"temp": plugin}, bus=bus)

        # Create an update lock for the plugin.
        mgr._get_update_lock("temp")
        assert "temp" in mgr._update_locks

        with patch("tachikoma.plugins.manager.shutil.rmtree"):
            await mgr.remove("temp")

        await bus.wait_until_idle()
        await bus.stop()

        assert "temp" not in mgr._update_locks
