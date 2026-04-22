"""Tests for the update apply module: editable detection and upgrade execution."""

import importlib.metadata
import json
import subprocess
from unittest.mock import MagicMock, patch

from tachikoma.updates.apply import _is_editable_install, run_upgrade

# ---------------------------------------------------------------------------
# _is_editable_install
# ---------------------------------------------------------------------------


class TestIsEditableInstall:
    def test_editable_install(self) -> None:
        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {"editable": True}})
        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            assert _is_editable_install() is True

    def test_non_editable_install(self) -> None:
        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})
        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            assert _is_editable_install() is False

    def test_missing_direct_url_json(self) -> None:
        dist = MagicMock()
        dist.read_text.return_value = None
        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            assert _is_editable_install() is False

    def test_missing_distribution(self) -> None:
        with patch(
            "tachikoma.updates.apply.importlib.metadata.distribution",
            side_effect=importlib.metadata.PackageNotFoundError,
        ):
            assert _is_editable_install() is False

    def test_malformed_json(self) -> None:
        dist = MagicMock()
        dist.read_text.return_value = "not json"
        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            assert _is_editable_install() is False

    def test_file_not_found(self) -> None:
        dist = MagicMock()
        dist.read_text.side_effect = FileNotFoundError
        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            assert _is_editable_install() is False


# ---------------------------------------------------------------------------
# run_upgrade
# ---------------------------------------------------------------------------


class TestRunUpgrade:
    def test_editable_install_returns_error(self) -> None:
        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {"editable": True}})
        with (
            patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist),
        ):
            result = run_upgrade()
        assert result.error == "editable install"
        assert not result.changed

    @patch("tachikoma.updates.apply.importlib.metadata.version")
    @patch("tachikoma.updates.apply.subprocess.run")
    def test_successful_upgrade(self, mock_run: MagicMock, mock_version: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_version.side_effect = ["1.0.0", "1.1.0"]

        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})

        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            result = run_upgrade()

        assert result.changed is True
        assert result.old_version == "1.0.0"
        assert result.new_version == "1.1.0"
        assert result.error is None

    @patch("tachikoma.updates.apply.importlib.metadata.version", return_value="1.0.0")
    @patch("tachikoma.updates.apply.subprocess.run")
    def test_already_up_to_date(self, mock_run: MagicMock, mock_version: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})

        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            result = run_upgrade()

        assert result.already_up_to_date is True
        assert not result.changed

    @patch("tachikoma.updates.apply.importlib.metadata.version", return_value="1.0.0")
    @patch("tachikoma.updates.apply.subprocess.run")
    def test_upgrade_failure(self, mock_run: MagicMock, mock_version: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="network error")

        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})

        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            result = run_upgrade()

        assert result.error is not None
        assert "exit code 1" in result.error
        assert not result.changed

    @patch("tachikoma.updates.apply.importlib.metadata.version", return_value="1.0.0")
    @patch("tachikoma.updates.apply.subprocess.run", side_effect=FileNotFoundError)
    def test_uv_not_found(self, mock_run: MagicMock, mock_version: MagicMock) -> None:
        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})

        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            result = run_upgrade()

        assert "uv not found" in result.error

    @patch("tachikoma.updates.apply.importlib.metadata.version", return_value="1.0.0")
    @patch(
        "tachikoma.updates.apply.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="uv", timeout=120),
    )
    def test_timeout(self, mock_run: MagicMock, mock_version: MagicMock) -> None:
        dist = MagicMock()
        dist.read_text.return_value = json.dumps({"dir_info": {}})

        with patch("tachikoma.updates.apply.importlib.metadata.distribution", return_value=dist):
            result = run_upgrade()

        assert "timed out" in result.error
