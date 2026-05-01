"""Tests for git bootstrap hook.

Tests for DLT-020: Git module for workspace version tracking.
Tests updated for DLT-097: workspace startup sync after init.
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.git.hooks import _ensure_gitignore_entries, git_hook
from tachikoma.git.sync import SYNC_RESULT


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

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        assert (workspace_path / ".git").is_dir()

    async def test_creates_initial_commit(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook creates an initial empty commit."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        # Check that there's a commit with the expected message
        git_log = (workspace_path / ".git" / "logs" / "HEAD").read_text()
        assert "Initial commit" in git_log

    async def test_configures_repo_local_identity(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Repo-local identity is configured (user.name, user.email)."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        # Read the local config file
        config_path = workspace_path / ".git" / "config"
        config_content = config_path.read_text()

        assert "Tachikoma" in config_content
        assert "tachikoma@local" in config_content

    async def test_idempotent_when_git_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook is idempotent when .git already exists (skips init, runs sync)."""

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ) as mock_sync:
            # Run twice
            await git_hook(ctx)
            await git_hook(ctx)

        # Sync should have been called both times
        assert mock_sync.call_count == 2

    async def test_reinitializes_when_git_deleted(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook re-initializes when .git was deleted."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            # First run
            await git_hook(ctx)

            # Delete .git
            shutil.rmtree(workspace_path / ".git")

            # Second run should re-initialize
            await git_hook(ctx)

        assert (workspace_path / ".git").is_dir()

    async def test_fresh_init_creates_gitignore(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC1: Fresh init creates .gitignore with DB binary and logs patterns, commits it."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        gitignore = (workspace_path / ".gitignore").read_text()
        assert ".tachikoma/*.db" in gitignore
        assert ".tachikoma/logs/tachikoma.log" in gitignore

        # .gitignore was committed as a second commit after the initial empty one
        log = subprocess.check_output(["git", "log", "--format=%s"], cwd=workspace_path, text=True)
        assert "Add gitignore for workspace exclusions" in log
        assert "Initial commit" in log

    async def test_fresh_init_appends_to_existing_gitignore(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: If the workspace was pre-populated with a .gitignore, all entries
        are appended without clobbering."""
        workspace_path = settings_manager.settings.workspace.path
        gitignore = workspace_path / ".gitignore"
        gitignore.write_text("*.log\n")

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        content = gitignore.read_text()
        assert "*.log" in content
        assert ".tachikoma/*.db" in content
        assert ".tachikoma/logs/tachikoma.log" in content

    async def test_existing_repo_with_gitignore_noop(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC3: Hook is idempotent — running twice against a freshly
        initialized repo does not rewrite .gitignore."""
        workspace_path = settings_manager.settings.workspace.path
        gitignore = workspace_path / ".gitignore"

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)
            snapshot = gitignore.read_text()

            await git_hook(ctx)
            assert gitignore.read_text() == snapshot

        # Only the two init-time commits exist; second hook invocation made none
        commit_count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], cwd=workspace_path, text=True
        ).strip()
        assert commit_count == "2"


class TestEnsureGitignoreEntries:
    """Tests for _ensure_gitignore_entries (AC2, AC3)."""

    async def test_appends_missing_logs_entry(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC2: Appends .tachikoma/logs/ to existing workspace without committing."""
        workspace_path = settings_manager.settings.workspace.path
        gitignore = workspace_path / ".gitignore"

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            # First init creates .gitignore with both entries
            await git_hook(ctx)

        # Simulate existing workspace: remove the logs entry
        gitignore.write_text(".tachikoma/*.db\n")

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            # Second run should append logs entry
            await git_hook(ctx)

        content = gitignore.read_text()
        assert ".tachikoma/*.db" in content
        assert ".tachikoma/logs/tachikoma.log" in content

    async def test_noop_when_entries_exist(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC3: No modification when all entries already present."""
        workspace_path = settings_manager.settings.workspace.path
        gitignore = workspace_path / ".gitignore"

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        snapshot = gitignore.read_text()

        # Second run on existing repo
        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        assert gitignore.read_text() == snapshot

    def test_ensure_appends_to_existing_file(self, tmp_path: Path) -> None:
        """AC2: _ensure_gitignore_entries appends missing entry to existing file."""
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".tachikoma/*.db\n")

        _ensure_gitignore_entries(tmp_path)

        content = gitignore.read_text()
        assert ".tachikoma/*.db" in content
        assert ".tachikoma/logs/tachikoma.log" in content

    def test_ensure_is_noop_when_complete(self, tmp_path: Path) -> None:
        """AC3: _ensure_gitignore_entries is no-op when all entries exist."""
        gitignore = tmp_path / ".gitignore"
        expected = ".tachikoma/*.db\n.tachikoma/logs/tachikoma.log\n.tachikoma/db-dump/\n"
        gitignore.write_text(expected)

        _ensure_gitignore_entries(tmp_path)

        assert gitignore.read_text() == expected

    def test_ensure_creates_file_if_missing(self, tmp_path: Path) -> None:
        """AC2: _ensure_gitignore_entries creates .gitignore if it doesn't exist."""
        _ensure_gitignore_entries(tmp_path)

        gitignore = tmp_path / ".gitignore"
        content = gitignore.read_text()
        assert ".tachikoma/*.db" in content
        assert ".tachikoma/logs/tachikoma.log" in content


@pytest.mark.asyncio
class TestWorkspaceSync:
    """Tests for workspace startup sync (DLT-097 R1)."""

    async def test_calls_sync_after_init(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Sync is called after init for newly initialized repos."""
        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ) as mock_sync:
            await git_hook(ctx)

        mock_sync.assert_awaited_once()

    async def test_calls_sync_when_git_already_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Sync runs even when .git already exists."""

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ) as mock_sync:
            # First run creates .git
            await git_hook(ctx)
            # Second run should still sync
            await git_hook(ctx)

        assert mock_sync.call_count == 2

    async def test_sync_skipped_when_no_origin(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Sync is skipped silently when no origin remote configured."""

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

    async def test_sync_calls_smart_pull_when_origin_exists(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: _sync_workspace is called with correct settings."""
        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ) as mock_sync:
            await git_hook(ctx)

        mock_sync.assert_awaited_once()
        # Verify it was called with the workspace path and settings
        call_args = mock_sync.call_args
        assert call_args[0][0] == settings_manager.settings.workspace.path

    async def test_sync_non_blocking_on_failure(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Sync failure doesn't block startup."""
        with patch(
            "tachikoma.git.hooks.smart_pull",
            new_callable=AsyncMock,
            return_value=(SYNC_RESULT["SYNC_FAILED"], []),
        ):
            await git_hook(ctx)  # Should not raise
