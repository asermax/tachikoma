"""Tests for the daily plugin update check tick function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from bubus import EventBus

from tachikoma.plugins.loader import LoadedPlugin
from tachikoma.plugins.sources import GitPluginSource
from tachikoma.plugins.state import PluginStateRepository
from tachikoma.plugins.updater import PluginUpdateInfo


def _make_git_plugin(alias: str) -> LoadedPlugin:
    return LoadedPlugin(
        alias=alias,
        source=GitPluginSource.model_validate(
            {
                "git": f"https://github.com/example/{alias}.git",
                "ref": "main",
            }
        ),
        manifest=None,
        status="loaded",
        diagnostic=None,
        plugin_dir=MagicMock(),
    )


class TestPluginCheckTick:
    @patch("tachikoma.__main__.dispatch_notification")
    @patch("tachikoma.__main__.run_daily_git_check")
    async def test_dispatches_notification_when_updates_found(
        self, mock_check, mock_dispatch
    ) -> None:
        from tachikoma.__main__ import _plugin_check_tick  # noqa: PLC0415

        mock_check.return_value = [
            PluginUpdateInfo(alias="plugin-a", available_version="b" * 40),
        ]

        manager = MagicMock()
        manager._loaded = {"plugin-a": _make_git_plugin("plugin-a")}
        state_repo = AsyncMock(spec=PluginStateRepository)
        bus = MagicMock(spec=EventBus)

        await _plugin_check_tick(manager, state_repo, bus)

        mock_check.assert_awaited_once_with(manager._loaded, state_repo)
        mock_dispatch.assert_awaited_once()
        call_kwargs = mock_dispatch.call_args
        assert "plugin-a" in call_kwargs.kwargs["content"]
        assert call_kwargs.kwargs["source"] == "Plugin Update Check"
        assert call_kwargs.kwargs["source_id"] == "plugin_update_check"

    @patch("tachikoma.__main__.dispatch_notification")
    @patch("tachikoma.__main__.run_daily_git_check")
    async def test_no_notification_when_no_updates(self, mock_check, mock_dispatch) -> None:
        from tachikoma.__main__ import _plugin_check_tick  # noqa: PLC0415

        mock_check.return_value = []

        manager = MagicMock()
        manager._loaded = {}
        state_repo = AsyncMock(spec=PluginStateRepository)
        bus = MagicMock(spec=EventBus)

        await _plugin_check_tick(manager, state_repo, bus)

        mock_check.assert_awaited_once()
        mock_dispatch.assert_not_awaited()

    @patch("tachikoma.__main__.dispatch_notification")
    @patch("tachikoma.__main__.run_daily_git_check")
    async def test_notification_includes_multiple_plugins(self, mock_check, mock_dispatch) -> None:
        from tachikoma.__main__ import _plugin_check_tick  # noqa: PLC0415

        mock_check.return_value = [
            PluginUpdateInfo(alias="plugin-a", available_version="a" * 40),
            PluginUpdateInfo(alias="plugin-b", available_version="b" * 40),
        ]

        manager = MagicMock()
        manager._loaded = {
            "plugin-a": _make_git_plugin("plugin-a"),
            "plugin-b": _make_git_plugin("plugin-b"),
        }
        state_repo = AsyncMock(spec=PluginStateRepository)
        bus = MagicMock(spec=EventBus)

        await _plugin_check_tick(manager, state_repo, bus)

        content = mock_dispatch.call_args.kwargs["content"]
        assert "plugin-a" in content
        assert "plugin-b" in content
        assert "update_plugin" in content
