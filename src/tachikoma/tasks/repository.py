"""TaskRepository: async SQLAlchemy persistence layer for task definitions and instances.

Owns the AsyncEngine lifecycle. All callers receive frozen dataclasses —
SQLAlchemy types never leak out of this module.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.model import (
    TaskBase,
    TaskDefinition,
    TaskDefinitionRecord,
    TaskInstance,
    TaskInstanceRecord,
    TaskStatus,
    TaskType,
)

_log = logger.bind(component="tasks")


class TaskRepository:
    """Async repository for task definitions and instances backed by SQLite via aiosqlite.

    Usage::

        repo = TaskRepository(data_path / "tasks.db")
        await repo.initialize()
        try:
            definition = await repo.create_definition(definition_obj)
            ...
        finally:
            await repo.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    async def initialize(self) -> None:
        """Create the async engine, session factory, and run database migrations.

        Idempotent: calling multiple times is safe.
        """
        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, echo=False)

        # expire_on_commit=False lets us access attributes after commit without refresh
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        # Run schema migrations
        await self._run_migrations()

        _log.info("Task repository initialized: db_path={path}", path=self._db_path)

    async def _run_migrations(self) -> None:
        """Run schema migrations using SQLAlchemy's create_all.

        For the tasks subsystem, we use a simpler approach than the sessions
        subsystem since we're starting fresh without legacy databases to migrate.
        """
        if self._engine is None:
            return

        # Ensure the database file's parent directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Use SQLAlchemy's create_all with checkfirst=True for idempotent creation
        async with self._engine.begin() as conn:
            await conn.run_sync(TaskBase.metadata.create_all)

        _log.debug("Schema migrations completed: db_path={path}", path=self._db_path)

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # ------------------------------------------------------------------
    # Definition CRUD operations
    # ------------------------------------------------------------------

    async def create_definition(self, definition: TaskDefinition) -> TaskDefinition:
        """Persist a new task definition and return it."""
        self._require_initialized()

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

            async with self._session_factory() as db:  # type: ignore[misc]
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to create task definition {definition.id}"
            ) from exc

    async def get_definition(self, definition_id: str) -> TaskDefinition | None:
        """Return the task definition with the given ID, or None if not found."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.id == definition_id
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to get task definition {definition_id}"
            ) from exc

    async def list_definitions(self) -> list[TaskDefinition]:
        """Return all task definitions."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(select(TaskDefinitionRecord))
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list task definitions") from exc

    async def list_enabled_definitions(self) -> list[TaskDefinition]:
        """Return all enabled task definitions."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.enabled == True  # noqa: E712
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list enabled task definitions") from exc

    async def update_definition(self, definition_id: str, **fields) -> None:
        """Update arbitrary fields on a task definition by ID.

        Accepted fields: name, schedule, task_type, prompt, notify, enabled,
        last_fired_at.
        """
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.id == definition_id
                    )
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
            raise TaskRepositoryError(
                f"Failed to update task definition {definition_id}"
            ) from exc

    async def delete_definition(self, definition_id: str) -> bool:
        """Delete a task definition by ID. Returns True if deleted."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskDefinitionRecord).where(
                        TaskDefinitionRecord.id == definition_id
                    )
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return False

                await db.delete(record)
                await db.commit()

            return True

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to delete task definition {definition_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Instance CRUD operations
    # ------------------------------------------------------------------

    async def create_instance(self, instance: TaskInstance) -> TaskInstance:
        """Persist a new task instance and return it."""
        self._require_initialized()

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

            async with self._session_factory() as db:  # type: ignore[misc]
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to create task instance {instance.id}"
            ) from exc

    async def get_instance(self, instance_id: str) -> TaskInstance | None:
        """Return the task instance with the given ID, or None if not found."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskInstanceRecord).where(
                        TaskInstanceRecord.id == instance_id
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(f"Failed to get task instance {instance_id}") from exc

    async def get_pending_instances(self, task_type: TaskType) -> list[TaskInstance]:
        """Return all pending task instances of the given type."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.status == "pending")
                    .where(TaskInstanceRecord.task_type == task_type)
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to get pending {task_type} instances"
            ) from exc

    async def get_active_instance_for_definition(
        self, definition_id: str
    ) -> TaskInstance | None:
        """Return pending or running instance for a definition, if any exists.

        Used for duplicate prevention — only one active instance per definition.
        """
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
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
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskInstanceRecord).where(
                        TaskInstanceRecord.id == instance_id
                    )
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return

                for key, value in fields.items():
                    setattr(record, key, value)

                await db.commit()

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to update task instance {instance_id}"
            ) from exc

    async def delete_instance(self, instance_id: str) -> bool:
        """Delete a task instance by ID. Returns True if deleted.

        Used for transient notification cleanup after delivery.
        """
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskInstanceRecord).where(
                        TaskInstanceRecord.id == instance_id
                    )
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return False

                await db.delete(record)
                await db.commit()

            return True

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to delete task instance {instance_id}"
            ) from exc

    async def mark_running_as_failed(self, reason: str) -> int:
        """Mark all running instances as failed with the given reason.

        Used for crash recovery on startup — any running instances from
        a previous run are failed because their executor processes are gone.

        Returns the number of instances marked as failed.
        """
        self._require_initialized()

        try:
            count = 0
            async with self._session_factory() as db:  # type: ignore[misc]
                result = await db.execute(
                    select(TaskInstanceRecord).where(
                        TaskInstanceRecord.status == "running"
                    )
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_initialized(self) -> None:
        if self._engine is None or self._session_factory is None:
            raise TaskRepositoryError(
                "TaskRepository is not initialized. Call initialize() first."
            )
