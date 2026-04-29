"""Tests for the rollback marker lifecycle and version rollback execution."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from tachikoma.updates.rollback import (
    clear_restart_notification,
    clear_rollback_marker,
    clear_rollback_notification,
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
