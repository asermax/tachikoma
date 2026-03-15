"""Tests for git bootstrap hook.

Tests for DLT-020: Git module for workspace version tracking.
"""

import shutil
from pathlib import Path

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.git.hooks import git_hook


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


class TestGitHook:
    """Tests for git_hook."""

    async def test_initializes_git_repo_when_none_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook initializes git repo when no .git exists."""
        workspace_path = settings_manager.settings.workspace.path

        await git_hook(ctx)

        assert (workspace_path / ".git").is_dir()

    async def test_creates_initial_commit(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates an initial empty commit."""
        workspace_path = settings_manager.settings.workspace.path

        await git_hook(ctx)

        # Check that there's a commit with the expected message
        git_log = (workspace_path / ".git" / "logs" / "HEAD").read_text()
        assert "Initial commit" in git_log

    async def test_configures_repo_local_identity(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Repo-local identity is configured (user.name, user.email)."""
        workspace_path = settings_manager.settings.workspace.path

        await git_hook(ctx)

        # Read the local config file
        config_path = workspace_path / ".git" / "config"
        config_content = config_path.read_text()

        assert "Tachikoma" in config_content
        assert "tachikoma@local" in config_content

    async def test_idempotent_when_git_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook is idempotent when .git already exists (skips, no error)."""
        workspace_path = settings_manager.settings.workspace.path

        # Run twice
        await git_hook(ctx)
        await git_hook(ctx)

        # Should still have a valid git repo
        assert (workspace_path / ".git").is_dir()

    async def test_reinitializes_when_git_deleted(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook re-initializes when .git was deleted."""
        workspace_path = settings_manager.settings.workspace.path

        # First run
        await git_hook(ctx)

        # Delete .git
        shutil.rmtree(workspace_path / ".git")

        # Second run should re-initialize
        await git_hook(ctx)

        assert (workspace_path / ".git").is_dir()

    async def test_no_gitignore_created(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No .gitignore is created."""
        workspace_path = settings_manager.settings.workspace.path

        await git_hook(ctx)

        assert not (workspace_path / ".gitignore").exists()

    async def test_works_without_global_git_config(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook works without global git config (uses repo-local identity)."""
        workspace_path = settings_manager.settings.workspace.path

        await git_hook(ctx)

        # Verify repo-local config exists
        config_path = workspace_path / ".git" / "config"
        assert config_path.exists()
        config_content = config_path.read_text()
        assert "Tachikoma" in config_content
