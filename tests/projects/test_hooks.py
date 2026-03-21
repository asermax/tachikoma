"""Tests for projects bootstrap hook."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tachikoma.projects.hooks import projects_hook


@pytest.fixture
def mock_context() -> MagicMock:
    """Create a mock BootstrapContext."""
    ctx = MagicMock()
    ctx.settings_manager.settings.workspace.path = Path("/workspace")
    return ctx


@pytest.mark.asyncio
class TestProjectsHook:
    """Tests for projects_hook function."""

    async def test_creates_projects_dir_when_missing(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Creates projects/ directory when it doesn't exist."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        projects_dir = tmp_path / "projects"

        assert not projects_dir.exists()

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await projects_hook(mock_context)

        assert projects_dir.exists()

    async def test_idempotent_when_projects_dir_exists(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Does not fail when projects/ already exists."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        (projects_dir / "existing_file.txt").write_text("test")

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await projects_hook(mock_context)  # Should not raise

        # Existing content preserved
        assert (projects_dir / "existing_file.txt").exists()

    async def test_no_op_when_no_submodules(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Completes without error when no submodules exist."""
        mock_context.settings_manager.settings.workspace.path = tmp_path

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await projects_hook(mock_context)  # Should not raise

    async def test_syncs_submodules_in_parallel(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Syncs all submodules in parallel."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

        call_order: list[str] = []

        async def mock_sync(workspace_path: Path, path: str) -> None:
            call_order.append(f"{path}_start")
            await asyncio.sleep(0.05)
            call_order.append(f"{path}_end")

        submodule_paths = ["projects/a", "projects/b", "projects/c"]

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=submodule_paths,
            ),
            patch(
                "tachikoma.projects.hooks._sync_submodule_with_retry",
                side_effect=mock_sync,
            ),
        ):
            await projects_hook(mock_context)

        # All should have started before any finished (parallel execution)
        for path in submodule_paths:
            assert call_order.index(f"{path}_start") < call_order.index(f"{path}_end")

    async def test_retries_once_on_failure(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Retries sync once on failure, then continues."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

        call_count = [0]

        async def failing_sync(workspace_path: Path, path: str) -> None:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("First attempt failed")
            # Second attempt succeeds (implicitly)

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/test"],
            ),
            patch(
                "tachikoma.projects.hooks._sync_submodule",
                side_effect=failing_sync,
            ),
        ):
            await projects_hook(mock_context)

        # Should have been called twice (initial + retry)
        assert call_count[0] == 2

    async def test_continues_on_persistent_failure(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Continues with other submodules when one persistently fails."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

        sync_results: list[str] = []

        async def mock_sync(workspace_path: Path, path: str) -> None:
            sync_results.append(path)
            if path == "projects/failing":
                raise RuntimeError("Persistent failure")

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/failing", "projects/success"],
            ),
            patch(
                "tachikoma.projects.hooks._sync_submodule",
                side_effect=mock_sync,
            ),
        ):
            await projects_hook(mock_context)

        # Both should have been attempted
        assert "projects/failing" in sync_results
        assert "projects/success" in sync_results

    async def test_checks_out_default_branch_after_pull(
        self, mock_context: MagicMock, tmp_path: Path
    ) -> None:
        """Verifies the sync flow: init → fetch → resolve → checkout → pull."""
        mock_context.settings_manager.settings.workspace.path = tmp_path
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

        calls: list[str] = []

        def track_call(name: str) -> AsyncMock:
            async def _track(*args: object, **kwargs: object) -> None:
                calls.append(name)
            return _track

        with (
            patch(
                "tachikoma.projects.hooks.list_submodules",
                new_callable=AsyncMock,
                return_value=["projects/test"],
            ),
            patch(
                "tachikoma.projects.hooks.init_submodule",
                side_effect=track_call("init"),
            ),
            patch(
                "tachikoma.projects.hooks.fetch",
                side_effect=track_call("fetch"),
            ),
            patch(
                "tachikoma.projects.hooks.resolve_default_branch",
                new_callable=AsyncMock,
                return_value="main",
            ),
            patch(
                "tachikoma.projects.hooks.checkout_branch",
                side_effect=track_call("checkout"),
            ),
            patch(
                "tachikoma.projects.hooks.pull",
                side_effect=track_call("pull"),
            ),
        ):
            await projects_hook(mock_context)

        # Verify order
        assert calls == ["init", "fetch", "checkout", "pull"]
