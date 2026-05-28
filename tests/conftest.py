"""Root pytest configuration and shared fixtures.

This file is auto-loaded by pytest and contains fixtures shared across test files.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tachikoma.config import SettingsManager


def _make_mock_coordinator() -> MagicMock:
    """Create a mock coordinator with critical defaults to prevent infinite loops.

    Must be used whenever a coordinator mock is injected into a TelegramChannel.
    ``has_deferred`` defaults to a truthy MagicMock, causing ``_drain_deferred_queue``'s
    while-loop to never terminate.
    """
    coordinator = MagicMock()
    coordinator.has_deferred = False
    return coordinator


@pytest.fixture
def mock_coordinator() -> MagicMock:
    """Pytest fixture variant of _make_mock_coordinator."""
    return _make_mock_coordinator()


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    """Create a SettingsManager with a temporary workspace path."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[workspace]\npath = "{tmp_path / "workspace"}"\n')
    return SettingsManager(config_path)
