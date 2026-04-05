"""Integration tests for TaskRepository.

Uses real SQLite databases in tmp_path (no mocking of the DB layer).
"""

from datetime import UTC, datetime

from sqlalchemy import select

from tachikoma.tasks.model import ScheduleConfig, TaskDefinitionRecord
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

    async def test_delete_nonexistent_returns_false(self, repo: TaskRepository) -> None:
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

    async def test_get_active_instance_for_definition(self, repo: TaskRepository) -> None:
        """AC: get_active_instance_for_definition returns pending or running."""
        await repo.create_instance(
            _make_instance("pending-1", definition_id="def-1", status="pending")
        )

        active = await repo.get_active_instance_for_definition("def-1")

        assert active is not None
        assert active.id == "pending-1"

    async def test_get_active_instance_excludes_completed(self, repo: TaskRepository) -> None:
        """AC: completed instances are not returned as active (backward-compat path)."""
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

    async def test_delete_nonexistent_instance_returns_false(self, repo: TaskRepository) -> None:
        """AC: deleting nonexistent instance ID returns False."""
        result = await repo.delete_instance("ghost")

        assert result is False


class TestGetActiveInstancePeriodAware:
    """Tests for period-aware duplicate detection via scheduled_for param (DLT-090 S2).

    See: docs/delta-specs/DLT-090.md (R2)
    """

    async def test_finds_pending_instance_matching_scheduled_for(
        self, repo: TaskRepository
    ) -> None:
        """AC: When scheduled_for provided, pending instance with matching time is returned."""
        match_time = datetime(2026, 4, 4, 9, 0, tzinfo=UTC)
        await repo.create_instance(
            _make_instance(
                "pending-1",
                definition_id="def-1",
                status="pending",
                scheduled_for=match_time,
            )
        )

        active = await repo.get_active_instance_for_definition("def-1", scheduled_for=match_time)

        assert active is not None
        assert active.id == "pending-1"

    async def test_finds_completed_instance_matching_scheduled_for(
        self, repo: TaskRepository
    ) -> None:
        """AC: Completed instance with matching scheduled_for is returned (new behavior)."""
        match_time = datetime(2026, 4, 4, 9, 0, tzinfo=UTC)
        await repo.create_instance(
            _make_instance(
                "completed-1",
                definition_id="def-1",
                status="completed",
                scheduled_for=match_time,
            )
        )

        active = await repo.get_active_instance_for_definition("def-1", scheduled_for=match_time)

        assert active is not None
        assert active.id == "completed-1"

    async def test_excludes_failed_instance_matching_scheduled_for(
        self, repo: TaskRepository
    ) -> None:
        """AC: Failed instance with matching scheduled_for is excluded (retry allowed)."""
        match_time = datetime(2026, 4, 4, 9, 0, tzinfo=UTC)
        await repo.create_instance(
            _make_instance(
                "failed-1",
                definition_id="def-1",
                status="failed",
                scheduled_for=match_time,
            )
        )

        active = await repo.get_active_instance_for_definition("def-1", scheduled_for=match_time)

        assert active is None

    async def test_no_match_when_scheduled_for_differs(self, repo: TaskRepository) -> None:
        """AC: Instance with different scheduled_for is not returned."""
        match_time = datetime(2026, 4, 4, 9, 0, tzinfo=UTC)
        other_time = datetime(2026, 4, 4, 10, 0, tzinfo=UTC)
        await repo.create_instance(
            _make_instance(
                "pending-1",
                definition_id="def-1",
                status="pending",
                scheduled_for=other_time,
            )
        )

        active = await repo.get_active_instance_for_definition("def-1", scheduled_for=match_time)

        assert active is None

    async def test_no_match_when_no_instances_exist(self, repo: TaskRepository) -> None:
        """AC: No instances at all returns None."""
        match_time = datetime(2026, 4, 4, 9, 0, tzinfo=UTC)

        active = await repo.get_active_instance_for_definition("def-1", scheduled_for=match_time)

        assert active is None


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


class TestCorruptedScheduleIsolation:
    """Tests for per-record error isolation with auto-disable of corrupted definitions."""

    async def test_corrupted_definition_disabled_and_others_returned(
        self, repo: TaskRepository
    ) -> None:
        """AC4: Corrupted definition is disabled; valid definitions are returned."""
        # Create a valid definition
        await repo.create_definition(_make_definition("valid-1", name="Valid Task"))

        # Insert a corrupted definition directly via the ORM layer
        async with repo._session_factory() as db:
            db.add(
                TaskDefinitionRecord(
                    id="corrupted-1",
                    name="Corrupted Task",
                    schedule="not-valid-json",  # bare invalid string
                    task_type="session",
                    prompt="test",
                    enabled=True,
                    created_at=_utcnow(),
                )
            )
            await db.commit()

        definitions = await repo.list_enabled_definitions()

        # Only the valid definition is returned
        assert len(definitions) == 1
        assert definitions[0].id == "valid-1"

        # The corrupted definition was auto-disabled
        async with repo._session_factory() as db:
            result = await db.execute(
                select(TaskDefinitionRecord).where(TaskDefinitionRecord.id == "corrupted-1")
            )
            record = result.scalar_one()
        assert record.enabled is False
