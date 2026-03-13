"""Root pytest configuration and shared fixtures.

This file is auto-loaded by pytest and contains fixtures shared across test files.
"""

from pathlib import Path

import pytest

from tachikoma.config import SettingsManager


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    """Create a SettingsManager with a temporary workspace path."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'[workspace]\npath = "{tmp_path / "workspace"}"\n')
    return SettingsManager(config_path)
