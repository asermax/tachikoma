"""WorkflowStateRepository: async SQLAlchemy persistence layer for workflow states.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.workflows.errors import WorkflowRepositoryError
from tachikoma.workflows.model import (
    WorkflowState,
    WorkflowStateRecord,
    _serialize_definition_snapshot,
    _serialize_step_states,
)

_log = logger.bind(component="workflows")


class WorkflowStateRepository:
    """Async repository for workflow states backed by SQLite via aiosqlite.

    Receives a shared session factory from the Database class.

    Usage::

        repo = WorkflowStateRepository(database.session_factory)
        state = await repo.create(state_obj)
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create(self, state: WorkflowState) -> WorkflowState:
        """Persist a new workflow state and return it.

        Checks for duplicate active states first (same skill_name + workflow_name).
        Raises WorkflowRepositoryError if an active state already exists.
        """
        try:
            # Check for duplicate active state
            existing = await self.get_active(state.skill_name, state.workflow_name)
            if existing is not None:
                raise WorkflowRepositoryError(
                    f"Active workflow state already exists for skill={state.skill_name}, "
                    f"workflow={state.workflow_name}. Use soft_delete() first or update() "
                    f"the existing state."
                )

            record = WorkflowStateRecord(
                id=state.id,
                skill_name=state.skill_name,
                workflow_name=state.workflow_name,
                current_step=state.current_step,
                step_states=_serialize_step_states(state.step_states),
                definition_snapshot=_serialize_definition_snapshot(state.definition_snapshot),
                scratchpad_path=state.scratchpad_path,
                deleted_at=state.deleted_at,
                created_at=state.created_at or datetime.now(UTC),
                updated_at=state.updated_at or datetime.now(UTC),
            )

            async with self._session_factory() as db:
                db.add(record)
                await db.commit()

            return record.to_domain()

        except WorkflowRepositoryError:
            raise
        except Exception as exc:
            raise WorkflowRepositoryError(f"Failed to create workflow state {state.id}") from exc

    async def get(self, workflow_id: str) -> WorkflowState | None:
        """Return the workflow state with the given ID, or None if not found.

        Only returns non-deleted states (WHERE deleted_at IS NULL).
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.id == workflow_id,
                        WorkflowStateRecord.deleted_at.is_(None),
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise WorkflowRepositoryError(f"Failed to get workflow state {workflow_id}") from exc

    async def get_active(self, skill_name: str, workflow_name: str) -> WorkflowState | None:
        """Return the active (non-deleted) workflow state for the given skill and workflow.

        Used for duplicate prevention. Returns None if no active state exists.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.skill_name == skill_name,
                        WorkflowStateRecord.workflow_name == workflow_name,
                        WorkflowStateRecord.deleted_at.is_(None),
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise WorkflowRepositoryError(
                f"Failed to get active workflow state for skill={skill_name}, "
                f"workflow={workflow_name}"
            ) from exc

    async def update(self, workflow_id: str, **fields) -> WorkflowState | None:
        """Update arbitrary fields on a workflow state by ID.

        Always bumps updated_at to datetime.now(UTC).

        Accepted fields: current_step, step_states, definition_snapshot,
        scratchpad_path.

        Returns the updated state or None if not found.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.id == workflow_id,
                        WorkflowStateRecord.deleted_at.is_(None),
                    )
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return None

                for key, value in fields.items():
                    if key == "step_states":
                        setattr(record, key, _serialize_step_states(value))
                    elif key == "definition_snapshot":
                        setattr(record, key, _serialize_definition_snapshot(value))
                    else:
                        setattr(record, key, value)

                record.updated_at = datetime.now(UTC)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise WorkflowRepositoryError(f"Failed to update workflow state {workflow_id}") from exc

    async def soft_delete(self, workflow_id: str) -> bool:
        """Soft delete a workflow state by ID (sets deleted_at).

        Returns True if deleted, False if not found.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.id == workflow_id,
                        WorkflowStateRecord.deleted_at.is_(None),
                    )
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return False

                record.deleted_at = datetime.now(UTC)
                await db.commit()

            return True

        except Exception as exc:
            raise WorkflowRepositoryError(
                f"Failed to soft delete workflow state {workflow_id}"
            ) from exc

    async def list_active(self) -> list[WorkflowState]:
        """Return all active (non-deleted) workflow states.

        Filters out deleted states (WHERE deleted_at IS NULL).
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(WorkflowStateRecord.deleted_at.is_(None))
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise WorkflowRepositoryError("Failed to list active workflow states") from exc

    async def list_stale(self, threshold: timedelta) -> list[WorkflowState]:
        """Return non-deleted workflow states with updated_at older than threshold.

        Used for crash recovery and cleanup of abandoned workflows.
        """
        try:
            cutoff = datetime.now(UTC) - threshold

            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.deleted_at.is_(None),
                        WorkflowStateRecord.updated_at < cutoff,
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise WorkflowRepositoryError("Failed to list stale workflow states") from exc
