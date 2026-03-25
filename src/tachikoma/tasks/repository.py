"""TaskRepository: async SQLAlchemy persistence layer for task definitions and instances.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.model import (
    TaskDefinition,
    TaskDefinitionRecord,
    TaskInstance,
    TaskInstanceRecord,
    TaskType,
)

_log = logger.bind(component="tasks")


class TaskRepository:
    """Async repository for task definitions and instances backed by SQLite via aiosqlite.

    Receives a shared session factory from the Database class.

    Usage::

        repo = TaskRepository(database.session_factory)
        definition = await repo.create_definition(definition_obj)
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Definition CRUD operations
    # ------------------------------------------------------------------

    async def create_definition(self, definition: TaskDefinition) -> TaskDefinition:
        """Persist a new task definition and return it."""
        try:
            record = TaskDefinitionRecord(
                id=definition.id,
                name=definition.name,
                schedule=definition.schedule.to_json(),
                task_type=definition.task_type,
                prompt=definition.prompt,
                notify=definition.notify,
                enabled=definition.enabled,
                last_fired_at=definition.last_fired_at,
                created_at=definition.created_at or datetime.now(UTC),
            )

            async with self._session_factory() as db:
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to create task definition {definition.id}") from exc

    async def get_definition(self, definition_id: str) -> TaskDefinition | None:
        """Return the task definition with the given ID, or None if not found."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskDefinitionRecord).where(TaskDefinitionRecord.id == definition_id)
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to get task definition {definition_id}") from exc

    async def list_definitions(self) -> list[TaskDefinition]:
        """Return all task definitions."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(select(TaskDefinitionRecord))
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list task definitions") from exc

    async def list_enabled_definitions(self) -> list[TaskDefinition]:
        """Return all enabled task definitions."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.enabled == True  # noqa: E712
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list enabled task definitions") from exc

    async def list_disabled_definitions(self) -> list[TaskDefinition]:
        """Return all disabled (archived) task definitions."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.enabled == False  # noqa: E712
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list disabled task definitions") from exc

    async def update_definition(self, definition_id: str, **fields) -> None:
        """Update arbitrary fields on a task definition by ID.

        Accepted fields: name, schedule, task_type, prompt, notify, enabled,
        last_fired_at.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskDefinitionRecord).where(TaskDefinitionRecord.id == definition_id)
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return

                for key, value in fields.items():
                    if key == "schedule" and hasattr(value, "to_json"):
                        setattr(record, key, value.to_json())
                    else:
                        setattr(record, key, value)

                await db.commit()

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to update task definition {definition_id}") from exc

    async def delete_definition(self, definition_id: str) -> bool:
        """Delete a task definition by ID. Returns True if deleted."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskDefinitionRecord).where(TaskDefinitionRecord.id == definition_id)
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return False

                await db.delete(record)
                await db.commit()

            return True

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to delete task definition {definition_id}") from exc

    # ------------------------------------------------------------------
    # Instance CRUD operations
    # ------------------------------------------------------------------

    async def create_instance(self, instance: TaskInstance) -> TaskInstance:
        """Persist a new task instance and return it."""
        try:
            record = TaskInstanceRecord(
                id=instance.id,
                definition_id=instance.definition_id,
                task_type=instance.task_type,
                status=instance.status,
                prompt=instance.prompt,
                scheduled_for=instance.scheduled_for,
                started_at=instance.started_at,
                completed_at=instance.completed_at,
                result=instance.result,
                created_at=instance.created_at or datetime.now(UTC),
            )

            async with self._session_factory() as db:
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to create task instance {instance.id}") from exc

    async def get_instance(self, instance_id: str) -> TaskInstance | None:
        """Return the task instance with the given ID, or None if not found."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord).where(TaskInstanceRecord.id == instance_id)
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to get task instance {instance_id}") from exc

    async def get_pending_instances(self, task_type: TaskType) -> list[TaskInstance]:
        """Return all pending task instances of the given type."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.status == "pending")
                    .where(TaskInstanceRecord.task_type == task_type)
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to get pending {task_type} instances") from exc

    async def get_active_instance_for_definition(self, definition_id: str) -> TaskInstance | None:
        """Return pending or running instance for a definition, if any exists.

        Used for duplicate prevention — only one active instance per definition.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.definition_id == definition_id)
                    .where(
                        TaskInstanceRecord.status.in_(["pending", "running"])  # noqa: S610
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to get active instance for definition {definition_id}"
            ) from exc

    async def update_instance(self, instance_id: str, **fields) -> None:
        """Update arbitrary fields on a task instance by ID.

        Accepted fields: status, started_at, completed_at, result.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord).where(TaskInstanceRecord.id == instance_id)
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return

                for key, value in fields.items():
                    setattr(record, key, value)

                await db.commit()

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to update task instance {instance_id}") from exc

    async def delete_instance(self, instance_id: str) -> bool:
        """Delete a task instance by ID. Returns True if deleted.

        Used for transient notification cleanup after delivery.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord).where(TaskInstanceRecord.id == instance_id)
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return False

                await db.delete(record)
                await db.commit()

            return True

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to delete task instance {instance_id}") from exc

    async def mark_running_as_failed(self, reason: str) -> int:
        """Mark all running instances as failed with the given reason.

        Used for crash recovery on startup — any running instances from
        a previous run are failed because their executor processes are gone.

        Returns the number of instances marked as failed.
        """
        try:
            count = 0
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord).where(TaskInstanceRecord.status == "running")
                )
                records = result.scalars().all()

                for record in records:
                    record.status = "failed"
                    record.completed_at = datetime.now(UTC)
                    record.result = f"Task failed: {reason}"
                    count += 1

                await db.commit()

            if count > 0:
                _log.warning(
                    "Crash recovery: marked {count} running instances as failed",
                    count=count,
                )

            return count

        except Exception as exc:
            raise TaskRepositoryError("Failed to mark running instances as failed") from exc
