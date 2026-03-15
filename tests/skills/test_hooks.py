"""Tests for skills bootstrap hook.

Tests for DLT-003: Skill system foundation and sub-agent delegation.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.skills.hooks import skills_hook


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(f'[workspace]\npath = "{workspace_path}"\n')
    return SettingsManager(config_path)


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace exists (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx


class TestSkillsHook:
    """Tests for skills_hook bootstrap."""

    async def test_creates_skills_directory_when_missing(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates skills/ directory when it doesn't exist."""
        workspace_path = settings_manager.settings.workspace.path

        await skills_hook(ctx)

        skills_path = workspace_path / "skills"
        assert skills_path.is_dir()

    async def test_idempotent_when_directory_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook is idempotent when skills/ already exists (skips, no error)."""
        workspace_path = settings_manager.settings.workspace.path

        # Run twice
        await skills_hook(ctx)
        await skills_hook(ctx)

        # Should still have a valid skills directory
        assert (workspace_path / "skills").is_dir()

    async def test_no_error_when_workspace_exists_but_skills_missing(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No error when workspace path exists but skills/ doesn't."""
        workspace_path = settings_manager.settings.workspace.path

        # Workspace exists from fixture, but skills/ doesn't
        assert not (workspace_path / "skills").exists()

        await skills_hook(ctx)

        # Should now exist
        assert (workspace_path / "skills").is_dir()

    async def test_permission_error_propagates(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: When mkdir raises PermissionError, the exception propagates.

        The Bootstrap class wraps this in BootstrapError, but the hook itself
        should let the exception propagate.
        """
        with (
            patch.object(
                Path,
                "mkdir",
                side_effect=PermissionError("Permission denied"),
            ),
            pytest.raises(PermissionError, match="Permission denied"),
        ):
            await skills_hook(ctx)
