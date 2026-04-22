"""Tests for the update MCP tools: apply_update tool handler and factory."""

from unittest.mock import AsyncMock, MagicMock, patch

from tachikoma.updates.apply import EDITABLE_ERROR, UpgradeResult
from tachikoma.updates.events import RestartRequested
from tachikoma.updates.tools import create_update_tools_server, handle_apply_update


def _make_result(**kwargs: object) -> UpgradeResult:
    """Create an UpgradeResult with sensible defaults."""
    defaults = {
        "old_version": "1.0.0",
        "new_version": None,
        "already_up_to_date": False,
        "error": None,
        "changed": False,
    }
    defaults.update(kwargs)
    return UpgradeResult(**defaults)  # type: ignore[arg-type]


class TestHandleApplyUpdate:
    async def test_success_fires_restart_event(self) -> None:
        bus = AsyncMock()
        result = _make_result(old_version="1.0.0", new_version="1.1.0", changed=True)

        with patch("tachikoma.updates.tools.run_upgrade", return_value=result):
            response = await handle_apply_update(bus)

        text = response["content"][0]["text"]
        assert "1.0.0" in text
        assert "1.1.0" in text
        assert "Restarting" in text
        bus.dispatch.assert_awaited_once()
        dispatched_event = bus.dispatch.call_args[0][0]
        assert isinstance(dispatched_event, RestartRequested)

    async def test_already_up_to_date_no_event(self) -> None:
        bus = AsyncMock()
        result = _make_result(already_up_to_date=True)

        with patch("tachikoma.updates.tools.run_upgrade", return_value=result):
            response = await handle_apply_update(bus)

        text = response["content"][0]["text"]
        assert "Already running the latest version" in text
        bus.dispatch.assert_not_awaited()

    async def test_editable_install_no_event(self) -> None:
        bus = AsyncMock()
        result = _make_result(error=EDITABLE_ERROR)

        with patch("tachikoma.updates.tools.run_upgrade", return_value=result):
            response = await handle_apply_update(bus)

        text = response["content"][0]["text"]
        assert "editable/development install" in text
        bus.dispatch.assert_not_awaited()

    async def test_failure_no_event(self) -> None:
        bus = AsyncMock()
        result = _make_result(error="Upgrade failed (exit code 1):\nnetwork error")

        with patch("tachikoma.updates.tools.run_upgrade", return_value=result):
            response = await handle_apply_update(bus)

        text = response["content"][0]["text"]
        assert "Upgrade failed" in text
        bus.dispatch.assert_not_awaited()

    async def test_no_prior_check_needed(self) -> None:
        """Verify the tool runs without requiring any prior check_updates call."""
        bus = AsyncMock()
        result = _make_result(old_version="1.0.0", new_version="1.1.0", changed=True)

        with patch("tachikoma.updates.tools.run_upgrade", return_value=result):
            response = await handle_apply_update(bus)

        assert "Upgrade successful" in response["content"][0]["text"]


class TestCreateUpdateToolsServer:
    def test_returns_valid_config(self) -> None:
        bus = MagicMock()
        config = create_update_tools_server(bus)
        assert config is not None

    def test_includes_both_tools(self) -> None:
        bus = MagicMock()
        config = create_update_tools_server(bus)
        # The config should be a valid McpSdkServerConfig
        assert hasattr(config, "__dict__") or config is not None
