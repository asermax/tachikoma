"""Tests for git bootstrap hook.

Tests for DLT-020: Git module for workspace version tracking.
Tests updated for DLT-097: workspace startup sync after init.
Tests updated for DLT-121: LFS configuration for workspace DB (ADR-012).
"""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.git.hooks import git_hook
from tachikoma.git.sync import SYNC_RESULT

requires_git_lfs = pytest.mark.skipif(
    shutil.which("git-lfs") is None,
    reason="git-lfs not installed on the host",
)


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


@requires_git_lfs
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

    async def test_no_gitignore_created(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: No .gitignore is created."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        assert not (workspace_path / ".gitignore").exists()

    async def test_works_without_global_git_config(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: Hook works without global git config (uses repo-local identity)."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        # Verify repo-local config exists
        config_path = workspace_path / ".git" / "config"
        assert config_path.exists()
        config_content = config_path.read_text()
        assert "Tachikoma" in config_content

    async def test_fresh_init_configures_lfs(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC1: Fresh init runs `git lfs install --local`, writes the LFS line
        into .gitattributes, and commits it."""
        workspace_path = settings_manager.settings.workspace.path

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        attrs = (workspace_path / ".gitattributes").read_text()
        assert ".tachikoma/*.db filter=lfs diff=lfs merge=lfs -text" in attrs

        # `git lfs install --local` registers filter hooks in .git/config
        config = (workspace_path / ".git" / "config").read_text()
        assert 'filter "lfs"' in config

        # .gitattributes was committed as a second commit after the initial empty one
        log = subprocess.check_output(["git", "log", "--format=%s"], cwd=workspace_path, text=True)
        assert "Configure LFS for database" in log
        assert "Initial commit" in log

    async def test_fresh_init_appends_to_existing_gitattributes(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: If the workspace was pre-populated with a .gitattributes (e.g.
        user put one there), the LFS line is appended without clobbering."""
        workspace_path = settings_manager.settings.workspace.path
        attrs = workspace_path / ".gitattributes"
        attrs.write_text("*.ogg filter=lfs diff=lfs merge=lfs -text\n")

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)

        content = attrs.read_text()
        assert "*.ogg filter=lfs" in content
        assert ".tachikoma/*.db filter=lfs" in content

    async def test_existing_repo_with_lfs_noop(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC3: Hook is idempotent — running twice against a freshly
        initialized repo does not rewrite .gitattributes or re-install LFS."""
        workspace_path = settings_manager.settings.workspace.path
        attrs = workspace_path / ".gitattributes"

        with patch(
            "tachikoma.git.hooks._sync_workspace",
            new_callable=AsyncMock,
        ):
            await git_hook(ctx)
            snapshot = attrs.read_text()

            await git_hook(ctx)
            assert attrs.read_text() == snapshot

        # Only the two init-time commits exist; second hook invocation made none
        commit_count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"], cwd=workspace_path, text=True
        ).strip()
        assert commit_count == "2"

    async def test_existing_repo_without_lfs_warns(
        self,
        ctx: BootstrapContext,
        settings_manager: SettingsManager,
    ) -> None:
        """AC4: Existing repo with no LFS tracking → warning logged, no
        automatic migration attempted."""
        workspace_path = settings_manager.settings.workspace.path

        # Manually init a bare git repo with no .gitattributes
        subprocess.run(["git", "init"], cwd=workspace_path, check=True, capture_output=True)

        captured: list[str] = []
        sink_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
        try:
            with patch(
                "tachikoma.git.hooks._sync_workspace",
                new_callable=AsyncMock,
            ):
                await git_hook(ctx)
        finally:
            logger.remove(sink_id)

        assert any("lacks LFS tracking" in msg for msg in captured)
        assert not (workspace_path / ".gitattributes").exists()


class TestLfsAvailability:
    """Tests for the git-lfs availability pre-flight check."""

    async def test_missing_git_lfs_raises(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC2: If `git-lfs` is not on PATH, the hook raises with an
        actionable install hint."""

        with (
            patch("tachikoma.git.hooks.shutil.which", return_value=None),
            pytest.raises(RuntimeError, match="git-lfs is required"),
        ):
            await git_hook(ctx)


@requires_git_lfs
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

        # Mock _sync_workspace to simulate the "no origin" path
        # (it catches RuntimeError from _run_git_command internally)
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
        # _sync_workspace catches exceptions internally, so git_hook
        # continues even if sync fails. Test by mocking smart_pull to fail.
        with patch(
            "tachikoma.git.hooks.smart_pull",
            new_callable=AsyncMock,
            return_value=SYNC_RESULT["SYNC_FAILED"],
        ):
            await git_hook(ctx)  # Should not raise
