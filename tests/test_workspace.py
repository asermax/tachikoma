"""Workspace initialization hook tests.

Tests for DLT-023: Bootstrap agent workspace on first run.
"""

from pathlib import Path

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.workspace import workspace_hook


class TestWorkspaceHook:
    """Tests for the workspace initialization hook."""

    @pytest.fixture
    def ctx(self, settings_manager: SettingsManager) -> BootstrapContext:
        return BootstrapContext(settings_manager=settings_manager, prompt=input)

    async def test_creates_workspace_and_data_dir(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
    ) -> None:
        """AC (R0, R1): No dirs exist, both workspace and .tachikoma/ are created."""
        await workspace_hook(ctx)

        ws = settings_manager.settings.workspace

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    async def test_skips_when_workspace_exists(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
    ) -> None:
        """AC (R7): Dirs already exist, no error on re-run."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)
        ws.data_path.mkdir()

        await workspace_hook(ctx)

        assert ws.path.is_dir()
        assert ws.data_path.is_dir()

    async def test_creates_data_dir_when_workspace_exists(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
    ) -> None:
        """AC (R1): Workspace exists but .tachikoma/ doesn't, creates it."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True)

        await workspace_hook(ctx)

        assert ws.data_path.is_dir()

    async def test_raises_when_path_is_file(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
    ) -> None:
        """AC (R9): Workspace path is a file, raises with clear error."""
        ws = settings_manager.settings.workspace
        ws.path.parent.mkdir(parents=True, exist_ok=True)
        ws.path.touch()

        with pytest.raises(RuntimeError, match="not a directory"):
            await workspace_hook(ctx)

    async def test_raises_on_permission_error(self, ctx: BootstrapContext, mocker) -> None:
        """AC (R9): mkdir fails with PermissionError, raises with clear message."""
        mocker.patch.object(Path, "mkdir", side_effect=PermissionError)

        with pytest.raises(RuntimeError, match="Permission denied"):
            await workspace_hook(ctx)
