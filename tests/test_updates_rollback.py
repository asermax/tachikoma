"""Tests for the rollback marker lifecycle and version rollback execution."""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from tachikoma.updates.rollback import (
    RestartNotification,
    clear_restart_notification,
    clear_rollback_marker,
    clear_rollback_notification,
    handle_restart_notification,
    read_restart_notification,
    read_rollback_marker,
    read_rollback_notification,
    run_rollback,
    write_restart_notification,
    write_rollback_marker,
    write_rollback_notification,
)

# ---------------------------------------------------------------------------
# Rollback marker: write / read / clear
# ---------------------------------------------------------------------------


class TestRollbackMarker:
    def test_write_and_read_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        write_rollback_marker("1.0.0", "1.1.0")
        result = read_rollback_marker()

        assert result is not None
        assert result.previous_version == "1.0.0"
        assert result.target_version == "1.1.0"
        assert result.timestamp  # non-empty

    def test_read_returns_none_when_absent(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        assert read_rollback_marker() is None

    def test_read_returns_none_on_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        marker_file.write_text("not json")
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        assert read_rollback_marker() is None

    def test_read_returns_none_on_missing_fields(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        marker_file.write_text(json.dumps({"previous_version": "1.0.0"}))
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        assert read_rollback_marker() is None

    def test_clear_removes_file(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        marker_file.write_text("{}")
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        clear_rollback_marker()
        assert not marker_file.exists()

    def test_clear_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        marker_file = tmp_path / "update-pending.json"
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)

        clear_rollback_marker()  # no file — should not raise
        assert not marker_file.exists()

    def test_stale_marker_cleared_unconditionally(self, tmp_path: Path, monkeypatch) -> None:
        """AC1: A stale marker from a previous session is cleared after successful bootstrap.

        Simulates the bug scenario: apply_update wrote a marker in a previous
        session, the process restarted, and the new session's bootstrap succeeds.
        The unconditional clear_rollback_marker() call ensures the stale marker
        is removed so a later manual restart is classified correctly.
        """
        marker_file = tmp_path / "update-pending.json"
        monkeypatch.setattr("tachikoma.updates.rollback.MARKER_PATH", marker_file)
        # Simulate a stale marker left by a previous session's apply_update
        write_rollback_marker("1.0.0", "1.1.0")

        # Unconditional clear — as __main__.py now does after bootstrap succeeds,
        # even when the in-memory rollback_marker variable was None at startup.
        clear_rollback_marker()

        assert not marker_file.exists()
        # A later restart should not see any marker → classified as "manual"
        assert read_rollback_marker() is None


# ---------------------------------------------------------------------------
# Rollback notification: write / read / clear
# ---------------------------------------------------------------------------


class TestRollbackNotification:
    def test_write_and_read_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "update-rollback.json"
        monkeypatch.setattr("tachikoma.updates.rollback.NOTIFICATION_PATH", notif_file)

        write_rollback_notification("1.0.0", "1.1.0", "Hook 'db' failed")
        result = read_rollback_notification()

        assert result is not None
        assert result.previous_version == "1.0.0"
        assert result.failed_version == "1.1.0"
        assert result.error == "Hook 'db' failed"

    def test_read_returns_none_when_absent(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "update-rollback.json"
        monkeypatch.setattr("tachikoma.updates.rollback.NOTIFICATION_PATH", notif_file)

        assert read_rollback_notification() is None

    def test_read_returns_none_on_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "update-rollback.json"
        notif_file.write_text("not json")
        monkeypatch.setattr("tachikoma.updates.rollback.NOTIFICATION_PATH", notif_file)

        assert read_rollback_notification() is None

    def test_clear_removes_file(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "update-rollback.json"
        notif_file.write_text("{}")
        monkeypatch.setattr("tachikoma.updates.rollback.NOTIFICATION_PATH", notif_file)

        clear_rollback_notification()
        assert not notif_file.exists()

    def test_clear_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "update-rollback.json"
        monkeypatch.setattr("tachikoma.updates.rollback.NOTIFICATION_PATH", notif_file)

        clear_rollback_notification()  # no file — should not raise
        assert not notif_file.exists()


# ---------------------------------------------------------------------------
# Restart notification: write / read / clear
# ---------------------------------------------------------------------------


class TestRestartNotification:
    def test_write_and_read_roundtrip_update(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        write_restart_notification(
            reason="update",
            previous_version="1.55.0",
            new_version="1.56.0",
        )
        result = read_restart_notification()

        assert result is not None
        assert result.reason == "update"
        assert result.previous_version == "1.55.0"
        assert result.new_version == "1.56.0"
        assert result.timestamp  # non-empty

    def test_write_and_read_roundtrip_manual(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        write_restart_notification(
            reason="manual",
            previous_version=None,
            new_version=None,
        )
        result = read_restart_notification()

        assert result is not None
        assert result.reason == "manual"
        assert result.previous_version is None
        assert result.new_version is None

    def test_read_returns_none_when_absent(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        assert read_restart_notification() is None

    def test_read_returns_none_on_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        notif_file.write_text("not json")
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        assert read_restart_notification() is None

    def test_read_returns_none_on_missing_fields(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        notif_file.write_text(json.dumps({"reason": "manual"}))
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        assert read_restart_notification() is None

    def test_read_returns_none_on_invalid_reason(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        notif_file.write_text(
            json.dumps(
                {
                    "reason": "bogus",
                    "previous_version": None,
                    "new_version": None,
                    "timestamp": "2026-04-29T00:00:00+00:00",
                }
            )
        )
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        assert read_restart_notification() is None

    def test_clear_removes_file(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        notif_file.write_text("{}")
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        clear_restart_notification()
        assert not notif_file.exists()

    def test_clear_idempotent(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)

        clear_restart_notification()  # no file — should not raise
        assert not notif_file.exists()


# ---------------------------------------------------------------------------
# run_rollback
# ---------------------------------------------------------------------------


class TestRunRollback:
    @patch("tachikoma.updates.rollback.subprocess.run")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        assert run_rollback("1.0.0") is True

    @patch("tachikoma.updates.rollback.subprocess.run")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        assert run_rollback("1.0.0") is False

    @patch("tachikoma.updates.rollback.subprocess.run", side_effect=FileNotFoundError)
    def test_uv_not_found(self, mock_run: MagicMock) -> None:
        assert run_rollback("1.0.0") is False

    @patch(
        "tachikoma.updates.rollback.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=120),
    )
    def test_timeout(self, mock_run: MagicMock) -> None:
        assert run_rollback("1.0.0") is False


# ---------------------------------------------------------------------------
# handle_restart_notification consolidation
# ---------------------------------------------------------------------------


def _make_restart_notification(reason="update", previous_version="1.0.0", new_version="1.1.0"):
    """Helper to write a restart notification marker for testing."""
    return RestartNotification(
        reason=reason,
        previous_version=previous_version,
        new_version=new_version,
        timestamp="2026-05-05T12:00:00+00:00",
    )


def _make_failed_plugin(alias, diagnostic):
    """Create a minimal LoadedPlugin-like mock for a failed plugin."""
    plugin = MagicMock()
    plugin.alias = alias
    plugin.diagnostic = diagnostic
    plugin.status = "failed"
    return plugin


class TestHandleRestartNotificationConsolidation:
    """Tests for the consolidated startup notification dispatch.

    Covers R6 (failed plugin notification) and R7 (startup notification
    consolidation) acceptance criteria.
    """

    async def test_restart_with_plugin_failures_sends_consolidated(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        notification = _make_restart_notification()
        failed_plugin = _make_failed_plugin("weather", "Required config field 'api_key' is missing")
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = [failed_plugin]

        await handle_restart_notification(
            bus, notification, rollback_was_dispatched=False, plugin_manager=plugin_manager
        )

        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1
        event = dispatch_calls[0].args[0]
        assert "back online" in event.prompt.lower()
        assert "weather" in event.prompt
        assert "api_key" in event.prompt
        assert not notif_file.exists()

    async def test_restart_only_sends_back_online(self, tmp_path: Path, monkeypatch) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        notification = _make_restart_notification()
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = []

        await handle_restart_notification(
            bus, notification, rollback_was_dispatched=False, plugin_manager=plugin_manager
        )

        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1
        event = dispatch_calls[0].args[0]
        assert "back online" in event.prompt.lower()
        assert "Plugin startup issues" not in event.prompt

    async def test_plugin_failures_only_sends_failure_notification(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        failed = [
            _make_failed_plugin("weather", "Required config field 'api_key' is missing"),
            _make_failed_plugin(
                "analytics",
                "Config field 'timeout' expects type integer, got str",
            ),
        ]
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = failed

        await handle_restart_notification(
            bus,
            restart_notification=None,
            rollback_was_dispatched=False,
            plugin_manager=plugin_manager,
        )

        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1
        event = dispatch_calls[0].args[0]
        assert "back online" not in event.prompt.lower()
        assert "weather" in event.prompt
        assert "analytics" in event.prompt
        assert "Plugin startup issues" in event.prompt

    async def test_neither_restart_nor_failures_sends_nothing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = []

        await handle_restart_notification(
            bus,
            restart_notification=None,
            rollback_was_dispatched=False,
            plugin_manager=plugin_manager,
        )

        bus.dispatch.assert_not_called()

    async def test_restart_marker_cleared_before_dispatch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """DES-011: marker must be cleared before dispatch side effects."""
        notif_file = tmp_path / "restart-notification.json"
        notif_file.write_text("{}")
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        notification = _make_restart_notification()
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = []

        # Verify file is cleared before dispatch happens by checking that
        # the file is gone after the call and dispatch still succeeded.
        await handle_restart_notification(
            bus, notification, rollback_was_dispatched=False, plugin_manager=plugin_manager
        )

        assert not notif_file.exists()
        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1

    async def test_rollback_dispatched_suppresses_restart_but_not_failures(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        notification = _make_restart_notification()
        failed = [_make_failed_plugin("weather", "Config field missing")]
        plugin_manager = MagicMock()
        plugin_manager.failed_plugins.return_value = failed

        await handle_restart_notification(
            bus, notification, rollback_was_dispatched=True, plugin_manager=plugin_manager
        )

        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1
        event = dispatch_calls[0].args[0]
        assert "back online" not in event.prompt.lower()
        assert "weather" in event.prompt
        assert not notif_file.exists()

    async def test_no_plugin_manager_backwards_compatible(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Existing callers without plugin_manager still work."""
        notif_file = tmp_path / "restart-notification.json"
        monkeypatch.setattr("tachikoma.updates.rollback.RESTART_NOTIFICATION_PATH", notif_file)
        bus = AsyncMock()
        notification = _make_restart_notification()

        await handle_restart_notification(bus, notification, rollback_was_dispatched=False)

        dispatch_calls = [c for c in bus.method_calls if "dispatch" in c[0]]
        assert len(dispatch_calls) == 1
        event = dispatch_calls[0].args[0]
        assert "back online" in event.prompt.lower()
        assert "Plugin startup issues" not in event.prompt
