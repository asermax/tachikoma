"""Tests for the update checker subsystem."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.app_state import AppStateRepository
from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.database import Database
from tachikoma.updates.checker import (
    DEDUP_KEY,
    UpdateCheckResult,
    check_for_update,
    fetch_latest_version,
    update_checker_tick,
)
from tachikoma.updates.hooks import updates_hook
from tachikoma.updates.tools import create_update_tools_server, handle_check_updates


# ---------------------------------------------------------------------------
# fetch_latest_version
# ---------------------------------------------------------------------------


class TestFetchLatestVersion:
    def test_success_returns_version(self) -> None:
        mock_body = json.dumps({"info": {"version": "1.43.0"}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("tachikoma.updates.checker.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_latest_version()

        assert result == "1.43.0"

    def test_network_error_returns_none(self) -> None:
        from urllib.error import URLError

        with patch(
            "tachikoma.updates.checker.urllib.request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            result = fetch_latest_version()

        assert result is None

    def test_malformed_json_returns_none(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("tachikoma.updates.checker.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_latest_version()

        assert result is None

    def test_missing_key_returns_none(self) -> None:
        mock_body = json.dumps({"info": {}}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("tachikoma.updates.checker.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_latest_version()

        assert result is None


# ---------------------------------------------------------------------------
# check_for_update
# ---------------------------------------------------------------------------


class TestCheckForUpdate:
    @patch("tachikoma.updates.checker.importlib.metadata.version", return_value="1.42.0")
    @patch("tachikoma.updates.checker.fetch_latest_version", return_value="1.43.0")
    def test_update_available(self, mock_fetch, mock_version) -> None:
        result = check_for_update()
        assert result.update_available is True
        assert result.current_version == "1.42.0"
        assert result.latest_version == "1.43.0"
        assert result.latest_is_prerelease is False

    @patch("tachikoma.updates.checker.importlib.metadata.version", return_value="1.43.0")
    @patch("tachikoma.updates.checker.fetch_latest_version", return_value="1.43.0")
    def test_already_on_latest(self, mock_fetch, mock_version) -> None:
        result = check_for_update()
        assert result.update_available is False

    @patch("tachikoma.updates.checker.importlib.metadata.version", return_value="1.44.0.dev1")
    @patch("tachikoma.updates.checker.fetch_latest_version", return_value="1.43.0")
    def test_dev_build_ahead(self, mock_fetch, mock_version) -> None:
        result = check_for_update()
        assert result.update_available is False

    @patch("tachikoma.updates.checker.importlib.metadata.version", return_value="1.42.0")
    @patch("tachikoma.updates.checker.fetch_latest_version", return_value="1.44.0b2")
    def test_prerelease_on_pypi(self, mock_fetch, mock_version) -> None:
        result = check_for_update()
        assert result.update_available is False
        assert result.latest_is_prerelease is True

    @patch("tachikoma.updates.checker.importlib.metadata.version", return_value="1.42.0")
    @patch("tachikoma.updates.checker.fetch_latest_version", return_value=None)
    def test_pypi_unreachable(self, mock_fetch, mock_version) -> None:
        result = check_for_update()
        assert result.update_available is False
        assert result.latest_version is None


# ---------------------------------------------------------------------------
# update_checker_tick
# ---------------------------------------------------------------------------


class TestUpdateCheckerTick:
    async def test_notify_on_new_version(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)
        bus = AsyncMock()

        result = UpdateCheckResult(
            current_version="1.42.0",
            latest_version="1.43.0",
            update_available=True,
            latest_is_prerelease=False,
        )

        with (
            patch("tachikoma.updates.checker.check_for_update", return_value=result),
            patch("tachikoma.updates.checker.dispatch_notification", new_callable=AsyncMock),
        ):
            await update_checker_tick(repo, bus)

        notified_version = await repo.get(DEDUP_KEY)
        assert notified_version == "1.43.0"

        await db.close()

    async def test_skip_on_already_notified(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)
        bus = AsyncMock()

        await repo.set(DEDUP_KEY, "1.43.0")

        result = UpdateCheckResult(
            current_version="1.42.0",
            latest_version="1.43.0",
            update_available=True,
            latest_is_prerelease=False,
        )

        with (
            patch("tachikoma.updates.checker.check_for_update", return_value=result),
            patch("tachikoma.updates.checker.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            await update_checker_tick(repo, bus)

        mock_dispatch.assert_not_called()

        await db.close()

    async def test_skip_on_no_update(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)
        bus = AsyncMock()

        result = UpdateCheckResult(
            current_version="1.43.0",
            latest_version="1.43.0",
            update_available=False,
            latest_is_prerelease=False,
        )

        with (
            patch("tachikoma.updates.checker.check_for_update", return_value=result),
            patch("tachikoma.updates.checker.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            await update_checker_tick(repo, bus)

        mock_dispatch.assert_not_called()

        await db.close()

    async def test_handle_db_error_gracefully(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)
        bus = AsyncMock()

        result = UpdateCheckResult(
            current_version="1.42.0",
            latest_version="1.43.0",
            update_available=True,
            latest_is_prerelease=False,
        )

        with (
            patch("tachikoma.updates.checker.check_for_update", return_value=result),
            patch.object(repo, "get", side_effect=Exception("db locked")),
            patch("tachikoma.updates.checker.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            await update_checker_tick(repo, bus)

        mock_dispatch.assert_not_called()

        await db.close()


# ---------------------------------------------------------------------------
# updates_hook
# ---------------------------------------------------------------------------


class TestUpdatesHook:
    async def test_stores_app_state_repository(self, settings_manager: SettingsManager) -> None:
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True, exist_ok=True)
        ws.data_path.mkdir(exist_ok=True)

        ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
        from tachikoma.database import database_hook

        await database_hook(ctx)
        await updates_hook(ctx)

        assert "app_state_repository" in ctx.extras
        assert isinstance(ctx.extras["app_state_repository"], AppStateRepository)

        await ctx.extras["database"].close()


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


class TestCheckUpdatesTool:
    async def test_returns_structured_result(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)

        result = UpdateCheckResult(
            current_version="1.42.0",
            latest_version="1.43.0",
            update_available=True,
            latest_is_prerelease=False,
        )

        with patch("tachikoma.updates.checker.check_for_update", return_value=result):
            response = await handle_check_updates(repo)

        assert response["content"][0]["type"] == "text"
        text = response["content"][0]["text"]
        assert "1.42.0" in text
        assert "1.43.0" in text
        assert "True" in text

        await db.close()

    async def test_no_side_effects(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()
        repo = AppStateRepository(db.session_factory)

        result = UpdateCheckResult(
            current_version="1.42.0",
            latest_version="1.43.0",
            update_available=True,
            latest_is_prerelease=False,
        )

        with (
            patch("tachikoma.updates.checker.check_for_update", return_value=result),
            patch("tachikoma.updates.checker.dispatch_notification", new_callable=AsyncMock) as mock_dispatch,
        ):
            await handle_check_updates(repo)

        mock_dispatch.assert_not_called()

        dedup = await repo.get(DEDUP_KEY)
        assert dedup is None

        await db.close()

    async def test_imports_work(self) -> None:
        from tachikoma.updates import (
            create_update_tools_server,
            update_checker_tick,
            updates_hook,
        )

        assert callable(update_checker_tick)
        assert callable(updates_hook)
        assert callable(create_update_tools_server)
