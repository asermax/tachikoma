"""Tests for the logging module.

Tests for DLT-013: Add structured logging for agent actions.
"""

import logging
from pathlib import Path
from time import sleep

import pytest
from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.coordinator import _log as coordinator_log
from tachikoma.logging import InterceptHandler, configure_logging, logging_hook


class TestConfigureLogging:
    """Tests for the configure_logging function."""

    def test_removes_default_handler_and_adds_file_handler(self, tmp_path: Path) -> None:
        """AC (R0, R4): Default handler removed, file handler added."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        # Reset logger state before test
        logger.remove()
        logger.add(lambda _: None)  # Add a dummy handler so remove() has something to remove

        configure_logging(level="INFO", data_path=data_path, console=False)

        # Verify log file was created after a log call
        logger.info("Test message")
        sleep(0.1)  # Allow enqueued writes to complete

        log_file = logs_dir / "tachikoma.log"
        assert log_file.exists()

        # Clean up
        logger.remove()

    def test_level_filtering_debug_filtered_at_info(self, tmp_path: Path) -> None:
        """AC (R3): DEBUG messages filtered when level is INFO."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        logger.debug("This should be filtered")
        logger.info("This should appear")
        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "This should appear" in content
        assert "This should be filtered" not in content

        logger.remove()

    def test_console_handler_added_when_enabled(self, tmp_path: Path, capsys) -> None:
        """AC (R9): Console handler added when console=True."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=True)

        logger.info("Test console message")
        sleep(0.1)

        err = capsys.readouterr().err
        assert "Test console message" in err

        logger.remove()

    def test_console_handler_not_added_when_disabled(self, tmp_path: Path, capsys) -> None:
        """AC (R9): No console output when console=False."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        logger.info("Test file only message")
        sleep(0.1)

        err = capsys.readouterr().err
        assert "Test file only message" not in err

        logger.remove()

    def test_intercept_handler_installed_on_stdlib_root(self, tmp_path: Path) -> None:
        """AC (R8): InterceptHandler installed on stdlib logging root."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        # Check that the root logger has an InterceptHandler
        root_handlers = logging.root.handlers
        assert any(isinstance(h, InterceptHandler) for h in root_handlers)

        logger.remove()

    def test_idempotent_handler_count(self, tmp_path: Path) -> None:
        """AC (R5): Calling configure_logging twice results in same handler count."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        # Count handlers after first call
        first_count = len(logger._core.handlers)

        # Call again
        configure_logging(level="INFO", data_path=data_path, console=False)

        # Count handlers after second call
        second_count = len(logger._core.handlers)

        assert first_count == second_count

        logger.remove()


class TestLoggingHook:
    """Tests for the logging_hook bootstrap hook."""

    async def test_creates_logs_directory(self, settings_manager: SettingsManager) -> None:
        """AC (R4): Hook creates logs/ directory under data path."""
        ctx = BootstrapContext(settings_manager=settings_manager, prompt=lambda q: "")

        await logging_hook(ctx)

        logs_dir = settings_manager.settings.workspace.data_path / "logs"
        assert logs_dir.exists()
        assert logs_dir.is_dir()

        # Clean up logger state
        logger.remove()

    async def test_raises_runtime_error_on_permission_denied(
        self, settings_manager: SettingsManager, mocker
    ) -> None:
        """AC (R4): Hook raises RuntimeError with 'Permission denied' on PermissionError."""
        ctx = BootstrapContext(settings_manager=settings_manager, prompt=lambda q: "")

        # Mock Path.mkdir to raise PermissionError
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError("Permission denied"))

        with pytest.raises(RuntimeError, match="Permission denied"):
            await logging_hook(ctx)

        logger.remove()

    async def test_existing_logs_dir_no_error(self, settings_manager: SettingsManager) -> None:
        """AC (R4): Hook succeeds when logs/ directory already exists."""
        # Create logs directory beforehand
        logs_dir = settings_manager.settings.workspace.data_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        ctx = BootstrapContext(settings_manager=settings_manager, prompt=lambda q: "")

        # Should not raise
        await logging_hook(ctx)

        assert logs_dir.exists()

        logger.remove()


class TestInterceptHandler:
    """Tests for the InterceptHandler stdlib bridge."""

    def test_stdlib_warning_appears_in_loguru(self, tmp_path: Path) -> None:
        """AC (R8): stdlib logging.warning appears in loguru output."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        # Use stdlib logging
        stdlib_logger = logging.getLogger("test.module")
        stdlib_logger.warning("Stdlib warning message")

        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "Stdlib warning message" in content

        logger.remove()

    def test_level_mapping_correct(self, tmp_path: Path) -> None:
        """AC (R8): stdlib WARNING level maps to loguru WARNING."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="DEBUG", data_path=data_path, console=False)

        # Use stdlib logging with different levels
        stdlib_logger = logging.getLogger("test.levels")
        stdlib_logger.debug("Debug from stdlib")
        stdlib_logger.info("Info from stdlib")
        stdlib_logger.warning("Warning from stdlib")
        stdlib_logger.error("Error from stdlib")

        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "DEBUG" in content
        assert "INFO" in content
        assert "WARNING" in content
        assert "ERROR" in content

        logger.remove()


class TestCoordinatorLogging:
    """Tests for coordinator log output."""

    def test_message_received_debug_log(self, tmp_path: Path) -> None:
        """AC (R1): Message received DEBUG log includes message length."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="DEBUG", data_path=data_path, console=False)

        coordinator_log.debug("Message received: length={n}", n=42)

        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "Message received" in content
        assert "length=42" in content

        logger.remove()

    def test_connected_info_log(self, tmp_path: Path) -> None:
        """AC (R1): Connected/disconnecting INFO logs fire on context enter/exit."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        coordinator_log.info("Connected to agent service")
        coordinator_log.info("Disconnecting from agent service")

        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "Connected to agent service" in content
        assert "Disconnecting from agent service" in content

        logger.remove()

    def test_stream_error_logged_with_error(self, tmp_path: Path) -> None:
        """AC (R1): Stream error logged with .error() (no traceback in output)."""
        data_path = tmp_path / ".tachikoma"
        logs_dir = data_path / "logs"
        logs_dir.mkdir(parents=True)

        logger.remove()
        configure_logging(level="INFO", data_path=data_path, console=False)

        coordinator_log.error("Stream error (recoverable): err={err}", err="test error")

        sleep(0.1)

        log_file = logs_dir / "tachikoma.log"
        content = log_file.read_text()

        assert "Stream error (recoverable)" in content
        assert "test error" in content
        # .error() does not include traceback
        assert "Traceback" not in content

        logger.remove()
