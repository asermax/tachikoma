"""Tests for memory bootstrap hook.

Tests for DLT-008: Extract and store memories from conversations.
"""

from pathlib import Path

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.memory.hooks import memory_hook


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(f'[workspace]\npath = "{workspace_path}"\n')
    return SettingsManager(config_path)


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace and data dirs exist (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.data_path.mkdir(exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx


class TestMemoryHook:
    """Tests for memory_hook."""

    async def test_creates_memories_directory_structure(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates all four directories (memories/, episodic/, facts/, preferences/)."""
        workspace_path = settings_manager.settings.workspace.path

        await memory_hook(ctx)

        memories_root = workspace_path / "memories"
        assert memories_root.is_dir()
        assert (memories_root / "episodic").is_dir()
        assert (memories_root / "facts").is_dir()
        assert (memories_root / "preferences").is_dir()

    async def test_idempotent_when_directories_exist(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Running twice produces no error and no change."""
        workspace_path = settings_manager.settings.workspace.path

        # Run twice
        await memory_hook(ctx)
        await memory_hook(ctx)

        # Verify directories still exist and are correct
        memories_root = workspace_path / "memories"
        assert memories_root.is_dir()
        assert (memories_root / "episodic").is_dir()
        assert (memories_root / "facts").is_dir()
        assert (memories_root / "preferences").is_dir()

    async def test_creates_subdirectories_inside_workspace_path(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Directories are created under the configured workspace path."""
        workspace_path = settings_manager.settings.workspace.path
        memories_root = workspace_path / "memories"

        await memory_hook(ctx)

        # Verify all paths are under workspace
        assert memories_root.is_relative_to(workspace_path)
        assert (memories_root / "episodic").is_relative_to(workspace_path)
        assert (memories_root / "facts").is_relative_to(workspace_path)
        assert (memories_root / "preferences").is_relative_to(workspace_path)
