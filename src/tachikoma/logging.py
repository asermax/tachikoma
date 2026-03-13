"""Logging infrastructure: structured logging via loguru.

Configures loguru with file-based output, rotation, and optional console output.
Installs an InterceptHandler to redirect stdlib logging through loguru.

DLT-013: Add structured logging for agent actions.
"""

import logging
import sys
from pathlib import Path

from loguru import logger

from tachikoma.bootstrap import BootstrapContext

# Structured text format for log entries
# See ADR-006 and DES-002 for format conventions
LOG_FORMAT = (
    "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[component]} | "
    "{name}:{function}:{line} - {message}"
)


class InterceptHandler(logging.Handler):
    """Redirects stdlib logging messages to loguru.

    This allows third-party libraries using stdlib logging to have their
    messages captured and formatted by loguru.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where the logged message originated
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def configure_logging(level: str, data_path: Path, console: bool = False) -> None:
    """Configure loguru with file output and optional console output.

    This function:
    1. Sets a global default component for unbound loggers
    2. Removes loguru's default stderr handler
    3. Adds a file handler with rotation, retention, and compression
    4. Optionally adds a colorized stderr handler for development
    5. Installs InterceptHandler to redirect stdlib logging

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        data_path: Path to the .tachikoma data directory
        console: If True, also output to stderr with colors

    """
    # Set global default for component so format string works
    # even for unbound loggers
    logger.configure(extra={"component": "core"})

    # Remove default stderr handler (ensures idempotency)
    logger.remove()

    # Add file handler with rotation, retention, and compression
    # See ADR-006: 100 MB rotation, 7 days retention, gzip compression
    logger.add(
        data_path / "logs" / "tachikoma.log",
        format=LOG_FORMAT,
        level=level,
        rotation="100 MB",
        retention="7 days",
        compression="gz",
        enqueue=True,
        encoding="utf-8",
        diagnose=True,
    )

    # Add console handler for development if enabled
    if console:
        logger.add(
            sys.stderr,
            format=LOG_FORMAT,
            level=level,
            colorize=True,
            diagnose=True,
        )

    # Install InterceptHandler to redirect stdlib logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


async def logging_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook to configure logging during workspace initialization.

    Creates the logs directory and configures loguru with settings from config.

    Args:
        ctx: Bootstrap context with settings_manager

    Raises:
        RuntimeError: If logs directory cannot be created (e.g., permissions)

    """
    settings = ctx.settings_manager.settings
    logs_dir = settings.workspace.data_path / "logs"

    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise RuntimeError(f"Cannot create logs directory: Permission denied: {logs_dir}") from e

    configure_logging(
        level=settings.logging.level,
        data_path=settings.workspace.data_path,
        console=settings.logging.console,
    )
