"""TaskRepository: async SQLAlchemy persistence layer for task definitions and instances.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

import json
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.db_utils import ensure_utc
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
                enabled=definition.enabled,
                last_fired_at=definition.last_fired_at,
                since=definition.since,
                created_at=definition.created_at or datetime.now(UTC),
                skills=json.dumps(list(definition.skills)),
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

            return await self._to_domains_with_isolation(records)

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

            return await self._to_domains_with_isolation(records)

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

            return await self._to_domains_with_isolation(records)

        except Exception as exc:
            raise TaskRepositoryError("Failed to list disabled task definitions") from exc

    async def update_definition(self, definition_id: str, **fields) -> None:
        """Update arbitrary fields on a task definition by ID.

        Accepted fields: name, schedule, task_type, prompt, enabled,
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
                    elif key == "skills":
                        setattr(record, key, json.dumps(list(value)))
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

    async def _to_domains_with_isolation(
        self, records: list[TaskDefinitionRecord]
    ) -> list[TaskDefinition]:
        """Convert records to domain objects, disabling corrupted definitions."""
        definitions: list[TaskDefinition] = []

        for record in records:
            try:
                definitions.append(record.to_domain())
            except (ValueError, TypeError) as exc:
                _log.warning(
                    "Disabling corrupted definition {id} ({name}): {err}",
                    id=record.id,
                    name=record.name,
                    err=exc,
                )
                try:
                    await self.update_definition(record.id, enabled=False)
                except Exception:
                    _log.exception(
                        "Failed to disable corrupted definition {id}",
                        id=record.id,
                    )

        return definitions

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
                sdk_session_id=instance.sdk_session_id,
                user_response=instance.user_response,
                updated_at=instance.updated_at,
                created_at=instance.created_at or datetime.now(UTC),
                workflow_id=instance.workflow_id,
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

    async def get_ready_background_instances(self) -> list[TaskInstance]:
        """Return background instances ready for execution.

        Ready = pending OR (waiting AND user_response IS NOT NULL).
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.task_type == "background")
                    .where(
                        or_(
                            TaskInstanceRecord.status == "pending",
                            and_(
                                TaskInstanceRecord.status == "waiting",
                                TaskInstanceRecord.user_response.is_not(None),
                            ),
                        )
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to get ready background instances") from exc

    async def list_expired_waiting_instances(
        self,
        timeout_seconds: int,
        *,
        only_workflow_tasks: bool | None = None,
    ) -> list[TaskInstance]:
        """Return waiting instances whose updated_at is older than timeout_seconds.

        NULL updated_at (legacy rows from before the column was tracked) are excluded.

        Args:
            timeout_seconds: Age threshold in seconds.
            only_workflow_tasks: When True, filter to workflow_id IS NOT NULL.
                When False, filter to workflow_id IS NULL. When None (default),
                no filter (backward compatible).
        """
        try:
            threshold = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
            async with self._session_factory() as db:
                query = (
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.status == "waiting")
                    .where(TaskInstanceRecord.updated_at.is_not(None))
                    .where(TaskInstanceRecord.updated_at < threshold)
                )

                if only_workflow_tasks is True:
                    query = query.where(TaskInstanceRecord.workflow_id.is_not(None))
                elif only_workflow_tasks is False:
                    query = query.where(TaskInstanceRecord.workflow_id.is_(None))

                result = await db.execute(query)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list expired waiting instances") from exc

    async def list_stuck_running_instances(self, timeout_seconds: int) -> list[TaskInstance]:
        """Return running instances whose started_at is older than timeout_seconds.

        NULL started_at (legacy rows) are excluded.
        """
        try:
            threshold = datetime.now(UTC) - timedelta(seconds=timeout_seconds)
            async with self._session_factory() as db:
                result = await db.execute(
                    select(TaskInstanceRecord)
                    .where(TaskInstanceRecord.status == "running")
                    .where(TaskInstanceRecord.started_at.is_not(None))
                    .where(TaskInstanceRecord.started_at < threshold)
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise TaskRepositoryError("Failed to list stuck running instances") from exc

    async def get_active_instance_for_definition(
        self,
        definition_id: str,
        scheduled_for: datetime | None = None,
    ) -> TaskInstance | None:
        """Return matching instance for a definition, if any exists.

        Used for duplicate prevention.

        When ``scheduled_for`` is provided, performs a period-aware duplicate
        check: matches instances where ``scheduled_for`` equals the given cron
        match time AND status is pending, running, or completed (failed is
        excluded to allow retry within the same period).

        When ``scheduled_for`` is None, preserves backward-compatible behavior:
        returns pending or running instances regardless of scheduled_for.
        """
        try:
            async with self._session_factory() as db:
                query = select(TaskInstanceRecord).where(
                    TaskInstanceRecord.definition_id == definition_id
                )

                if scheduled_for is not None:
                    query = query.where(
                        TaskInstanceRecord.scheduled_for == scheduled_for,
                        TaskInstanceRecord.status.in_(  # noqa: S610
                            ["pending", "running", "waiting", "completed"]
                        ),
                    )
                else:
                    query = query.where(
                        TaskInstanceRecord.status.in_(["pending", "running", "waiting"])  # noqa: S610
                    )

                result = await db.execute(query)
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise TaskRepositoryError(
                f"Failed to get active instance for definition {definition_id}"
            ) from exc

    async def update_instance(self, instance_id: str, **fields) -> None:
        """Update arbitrary fields on a task instance by ID.

        Accepted fields: status, started_at, completed_at, result,
        sdk_session_id, user_response, updated_at (auto-stamped by onupdate).
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

    async def cleanup_expired_one_shot_definitions(self, retention_hours: int) -> int:
        """Delete one-shot definitions whose retention window has expired.

        Eligible definitions are type=once AND last_fired_at IS NOT NULL AND
        every associated instance is terminal (completed or failed). The
        retention anchor is ``max(instance.completed_at)`` when any instance
        exists, otherwise ``last_fired_at``. Instances are deleted before
        the definition to respect FK constraints.

        Returns the number of definitions deleted.
        """
        try:
            threshold = datetime.now(UTC) - timedelta(hours=retention_hours)
            deleted = 0

            async with self._session_factory() as db:
                # Candidate one-shots: fired at least once, no active instances
                # (json.dumps serialization produces `"type": "once"` with a space)
                non_terminal_exists = (
                    select(TaskInstanceRecord.id)
                    .where(TaskInstanceRecord.definition_id == TaskDefinitionRecord.id)
                    .where(TaskInstanceRecord.status.not_in(["completed", "failed"]))  # noqa: S610
                    .exists()
                )

                candidates_result = await db.execute(
                    select(TaskDefinitionRecord)
                    .where(TaskDefinitionRecord.schedule.contains('"type": "once"'))
                    .where(TaskDefinitionRecord.last_fired_at.is_not(None))
                    .where(~non_terminal_exists)
                )
                candidates = candidates_result.scalars().all()

                for record in candidates:
                    latest_completed = await db.scalar(
                        select(func.max(TaskInstanceRecord.completed_at)).where(
                            TaskInstanceRecord.definition_id == record.id
                        )
                    )
                    anchor = ensure_utc(latest_completed) or ensure_utc(record.last_fired_at)

                    if anchor is None or anchor >= threshold:
                        continue

                    await db.execute(
                        delete(TaskInstanceRecord).where(
                            TaskInstanceRecord.definition_id == record.id
                        )
                    )
                    await db.delete(record)
                    deleted += 1

                await db.commit()

            if deleted > 0:
                _log.info(
                    "Cleaned up {count} expired one-shot definitions",
                    count=deleted,
                )

            return deleted

        except Exception as exc:
            raise TaskRepositoryError("Failed to cleanup expired one-shot definitions") from exc
