"""Integration tests for TaskRepository.

Uses real SQLite databases in tmp_path (no mocking of the DB layer).
"""

from datetime import UTC, datetime

from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository

from .conftest import _make_definition, _make_instance, _utcnow


class TestRepositoryDefinitionCRUD:
    """Tests for definition CRUD operations."""

    async def test_create_and_retrieve_definition(self, repo: TaskRepository) -> None:
        """AC: create then get_definition returns the definition."""
        definition = _make_definition("def-1", name="Test Task")
        await repo.create_definition(definition)

        retrieved = await repo.get_definition("def-1")

        assert retrieved is not None
        assert retrieved.id == "def-1"
        assert retrieved.name == "Test Task"

    async def test_create_preserves_schedule(self, repo: TaskRepository) -> None:
        """AC: schedule round-trips through the database."""
        schedule = ScheduleConfig(type="cron", expression="*/5 * * * *")
        definition = _make_definition("def-2", schedule=schedule)
        await repo.create_definition(definition)

        retrieved = await repo.get_definition("def-2")

        assert retrieved is not None
        assert retrieved.schedule.type == "cron"
        assert retrieved.schedule.expression == "*/5 * * * *"

    async def test_create_one_shot_definition(self, repo: TaskRepository) -> None:
        """AC: one-shot schedule is persisted correctly."""
        target = datetime(2026, 3, 22, 10, 0, tzinfo=UTC)
        schedule = ScheduleConfig(type="once", at=target)
        definition = _make_definition("def-3", schedule=schedule)
        await repo.create_definition(definition)

        retrieved = await repo.get_definition("def-3")

        assert retrieved is not None
        assert retrieved.schedule.type == "once"
        assert retrieved.schedule.at == target

    async def test_list_definitions(self, repo: TaskRepository) -> None:
        """AC: list_definitions returns all definitions."""
        await repo.create_definition(_make_definition("def-1"))
        await repo.create_definition(_make_definition("def-2"))
        await repo.create_definition(_make_definition("def-3"))

        definitions = await repo.list_definitions()

        assert len(definitions) == 3
        ids = {d.id for d in definitions}
        assert "def-1" in ids
        assert "def-2" in ids
        assert "def-3" in ids

    async def test_list_enabled_definitions(self, repo: TaskRepository) -> None:
        """AC: list_enabled_definitions filters by enabled=True."""
        await repo.create_definition(_make_definition("enabled-1", enabled=True))
        await repo.create_definition(_make_definition("disabled-1", enabled=False))
        await repo.create_definition(_make_definition("enabled-2", enabled=True))

        enabled = await repo.list_enabled_definitions()

        assert len(enabled) == 2
        ids = {d.id for d in enabled}
        assert "enabled-1" in ids
        assert "enabled-2" in ids
        assert "disabled-1" not in ids

    async def test_list_disabled_definitions(self, repo: TaskRepository) -> None:
        """AC: list_disabled_definitions filters by enabled=False."""
        await repo.create_definition(_make_definition("enabled-1", enabled=True))
        await repo.create_definition(_make_definition("disabled-1", enabled=False))
        await repo.create_definition(_make_definition("disabled-2", enabled=False))

        disabled = await repo.list_disabled_definitions()

        assert len(disabled) == 2
        ids = {d.id for d in disabled}
        assert "disabled-1" in ids
        assert "disabled-2" in ids
        assert "enabled-1" not in ids

    async def test_update_definition(self, repo: TaskRepository) -> None:
        """AC: update_definition modifies fields."""
        await repo.create_definition(_make_definition("def-1", name="Original"))
        new_schedule = ScheduleConfig(type="cron", expression="0 10 * * *")

        await repo.update_definition("def-1", name="Updated", schedule=new_schedule)

        retrieved = await repo.get_definition("def-1")
        assert retrieved is not None
        assert retrieved.name == "Updated"
        assert retrieved.schedule.expression == "0 10 * * *"

    async def test_update_definition_last_fired_at(self, repo: TaskRepository) -> None:
        """AC: last_fired_at can be updated."""
        await repo.create_definition(_make_definition("def-1"))
        fired_at = _utcnow()

        await repo.update_definition("def-1", last_fired_at=fired_at)

        retrieved = await repo.get_definition("def-1")
        assert retrieved is not None
        assert retrieved.last_fired_at == fired_at

    async def test_update_nonexistent_is_noop(self, repo: TaskRepository) -> None:
        """AC: updating an ID that doesn't exist raises no error."""
        await repo.update_definition("ghost", name="Ghost")

    async def test_delete_definition(self, repo: TaskRepository) -> None:
        """AC: delete_definition removes the definition."""
        await repo.create_definition(_make_definition("def-1"))

        result = await repo.delete_definition("def-1")

        assert result is True
        retrieved = await repo.get_definition("def-1")
        assert retrieved is None

    async def test_delete_nonexistent_returns_false(
        self, repo: TaskRepository
    ) -> None:
        """AC: deleting nonexistent ID returns False."""
        result = await repo.delete_definition("ghost")

        assert result is False


class TestRepositoryInstanceCRUD:
    """Tests for instance CRUD operations."""

    async def test_create_and_retrieve_instance(self, repo: TaskRepository) -> None:
        """AC: create then get_instance returns the instance."""
        instance = _make_instance("inst-1", definition_id="def-1")
        await repo.create_instance(instance)

        retrieved = await repo.get_instance("inst-1")

        assert retrieved is not None
        assert retrieved.id == "inst-1"
        assert retrieved.definition_id == "def-1"

    async def test_create_transient_instance(self, repo: TaskRepository) -> None:
        """AC: instances with null definition_id can be created."""
        instance = _make_instance("transient-1", definition_id=None)
        await repo.create_instance(instance)

        retrieved = await repo.get_instance("transient-1")

        assert retrieved is not None
        assert retrieved.definition_id is None

    async def test_get_pending_instances(self, repo: TaskRepository) -> None:
        """AC: get_pending_instances filters by status and type."""
        await repo.create_instance(
            _make_instance("pending-1", task_type="session", status="pending")
        )
        await repo.create_instance(
            _make_instance("pending-2", task_type="session", status="pending")
        )
        await repo.create_instance(
            _make_instance("running-1", task_type="session", status="running")
        )
        await repo.create_instance(
            _make_instance("pending-bg-1", task_type="background", status="pending")
        )

        pending_session = await repo.get_pending_instances("session")

        assert len(pending_session) == 2
        ids = {i.id for i in pending_session}
        assert "pending-1" in ids
        assert "pending-2" in ids
        assert "running-1" not in ids
        assert "pending-bg-1" not in ids

    async def test_get_active_instance_for_definition(
        self, repo: TaskRepository
    ) -> None:
        """AC: get_active_instance_for_definition returns pending or running."""
        await repo.create_instance(
            _make_instance("pending-1", definition_id="def-1", status="pending")
        )

        active = await repo.get_active_instance_for_definition("def-1")

        assert active is not None
        assert active.id == "pending-1"

    async def test_get_active_instance_excludes_completed(
        self, repo: TaskRepository
    ) -> None:
        """AC: completed instances are not returned as active."""
        await repo.create_instance(
            _make_instance("completed-1", definition_id="def-1", status="completed")
        )

        active = await repo.get_active_instance_for_definition("def-1")

        assert active is None

    async def test_update_instance_status(self, repo: TaskRepository) -> None:
        """AC: instance status can be updated."""
        await repo.create_instance(_make_instance("inst-1", status="pending"))

        await repo.update_instance("inst-1", status="running")

        retrieved = await repo.get_instance("inst-1")
        assert retrieved is not None
        assert retrieved.status == "running"

    async def test_update_instance_completion(self, repo: TaskRepository) -> None:
        """AC: instance completion fields can be updated."""
        await repo.create_instance(_make_instance("inst-1", status="running"))
        completed_at = _utcnow()

        await repo.update_instance(
            "inst-1", status="completed", completed_at=completed_at, result="Success"
        )

        retrieved = await repo.get_instance("inst-1")
        assert retrieved is not None
        assert retrieved.status == "completed"
        assert retrieved.completed_at == completed_at
        assert retrieved.result == "Success"

    async def test_delete_instance(self, repo: TaskRepository) -> None:
        """AC: delete_instance removes the instance."""
        await repo.create_instance(_make_instance("inst-1"))

        result = await repo.delete_instance("inst-1")

        assert result is True
        retrieved = await repo.get_instance("inst-1")
        assert retrieved is None

    async def test_delete_nonexistent_instance_returns_false(
        self, repo: TaskRepository
    ) -> None:
        """AC: deleting nonexistent instance ID returns False."""
        result = await repo.delete_instance("ghost")

        assert result is False


class TestRepositoryCrashRecovery:
    """Tests for crash recovery functionality."""

    async def test_mark_running_as_failed(self, repo: TaskRepository) -> None:
        """AC: mark_running_as_failed marks all running instances as failed."""
        await repo.create_instance(_make_instance("running-1", status="running"))
        await repo.create_instance(_make_instance("running-2", status="running"))
        await repo.create_instance(_make_instance("pending-1", status="pending"))

        count = await repo.mark_running_as_failed("system restart")

        assert count == 2

        inst1 = await repo.get_instance("running-1")
        inst2 = await repo.get_instance("running-2")
        pending = await repo.get_instance("pending-1")

        assert inst1 is not None
        assert inst1.status == "failed"
        assert "system restart" in (inst1.result or "")

        assert inst2 is not None
        assert inst2.status == "failed"

        assert pending is not None
        assert pending.status == "pending"  # Unchanged

    async def test_mark_running_as_failed_no_running(self, repo: TaskRepository) -> None:
        """AC: mark_running_as_failed returns 0 when no running instances."""
        await repo.create_instance(_make_instance("pending-1", status="pending"))

        count = await repo.mark_running_as_failed("system restart")

        assert count == 0
