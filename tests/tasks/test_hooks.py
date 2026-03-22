"""Integration tests for the task subsystem bootstrap hook.

Tests for DLT-010: Queue and process background tasks during idle time.
"""

from datetime import UTC, datetime

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.tasks.hooks import tasks_hook
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.model import TaskInstance


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace and data dirs exist (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.data_path.mkdir(exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx

    # Close the repository if the hook created one, to release SQLite connections
    repo = ctx.extras.get("task_repository")
    if repo is not None:
        await repo.close()


class TestTasksHook:
    """Tests for tasks_hook."""

    async def test_stores_repository_in_extras(self, ctx: BootstrapContext) -> None:
        """AC: hook stores repository in ctx.extras['task_repository']."""
        await tasks_hook(ctx)

        assert "task_repository" in ctx.extras
        assert isinstance(ctx.extras["task_repository"], TaskRepository)

    async def test_creates_database_file(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: hook creates the tasks.db file in the data directory."""
        db_path = settings_manager.settings.workspace.data_path / "tasks.db"
        assert not db_path.exists()

        await tasks_hook(ctx)

        assert db_path.exists()

    async def test_crash_recovery_marks_running_as_failed(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: hook marks any running instances as failed on startup."""
        data_path = settings_manager.settings.workspace.data_path

        # Pre-populate a database with a running instance
        repo = TaskRepository(data_path / "tasks.db")
        await repo.initialize()

        running_instance = TaskInstance(
            id="running-abc",
            definition_id=None,
            task_type="background",
            status="running",
            prompt="Running task",
            scheduled_for=datetime.now(UTC),
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        await repo.create_instance(running_instance)
        await repo.close()

        # Run the hook — should mark running as failed
        await tasks_hook(ctx)

        # Verify the instance was marked as failed
        repo2: TaskRepository = ctx.extras["task_repository"]
        recovered = await repo2.get_instance("running-abc")

        assert recovered is not None
        assert recovered.status == "failed"
        assert "system restart" in (recovered.result or "")

    async def test_idempotent_when_no_running_instances(
        self, ctx: BootstrapContext
    ) -> None:
        """AC: hook with no running instances completes without error."""
        await tasks_hook(ctx)

        repo: TaskRepository = ctx.extras["task_repository"]
        pending = await repo.get_pending_instances("session")
        assert len(pending) == 0

    async def test_only_marks_running_not_pending(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: hook only marks 'running' instances, not 'pending' ones."""
        data_path = settings_manager.settings.workspace.data_path

        # Pre-populate with both running and pending instances
        repo = TaskRepository(data_path / "tasks.db")
        await repo.initialize()

        running = TaskInstance(
            id="running-xyz",
            definition_id=None,
            task_type="background",
            status="running",
            prompt="Running task",
            scheduled_for=datetime.now(UTC),
            started_at=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        pending = TaskInstance(
            id="pending-xyz",
            definition_id=None,
            task_type="session",
            status="pending",
            prompt="Pending task",
            scheduled_for=datetime.now(UTC),
            created_at=datetime.now(UTC),
        )
        await repo.create_instance(running)
        await repo.create_instance(pending)
        await repo.close()

        # Run the hook
        await tasks_hook(ctx)

        # Verify only running was marked as failed
        repo2: TaskRepository = ctx.extras["task_repository"]

        recovered_running = await repo2.get_instance("running-xyz")
        assert recovered_running is not None
        assert recovered_running.status == "failed"

        recovered_pending = await repo2.get_instance("pending-xyz")
        assert recovered_pending is not None
        assert recovered_pending.status == "pending"
