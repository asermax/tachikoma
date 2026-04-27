"""WorkflowStateRepository: async SQLAlchemy persistence layer for workflow states.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

import json
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.workflows.composition import CreateChild, MutationBatch, SoftDelete, UpdateState
from tachikoma.workflows.errors import WorkflowRepositoryError
from tachikoma.workflows.model import (
    WorkflowState,
    WorkflowStateRecord,
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

        Duplicate prevention applies only to top-level instances
        (``parent_workflow_id IS NULL``).  Composed children are exempt
        from the ``(skill, workflow)`` uniqueness check (R10).
        """
        try:
            # Only enforce uniqueness for top-level workflows
            if state.parent_workflow_id is None:
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
                parent_workflow_id=state.parent_workflow_id,
                parent_step_id=state.parent_step_id,
                current_step=state.current_step,
                step_states=json.dumps(state.step_states),
                definition_snapshot=json.dumps(state.definition_snapshot),
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
        """Return the active top-level (non-deleted) workflow state for the
        given skill and workflow.

        Filters to top-level only (``parent_workflow_id IS NULL``) so that
        composed children are exempt from the uniqueness check (R10).
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.skill_name == skill_name,
                        WorkflowStateRecord.workflow_name == workflow_name,
                        WorkflowStateRecord.deleted_at.is_(None),
                        WorkflowStateRecord.parent_workflow_id.is_(None),
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
                    if key in ("step_states", "definition_snapshot"):
                        setattr(record, key, json.dumps(value))
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
        """Return all active top-level (non-deleted) workflow states.

        Filters to top-level only (``parent_workflow_id IS NULL``) per R11.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.deleted_at.is_(None),
                        WorkflowStateRecord.parent_workflow_id.is_(None),
                    )
                )
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise WorkflowRepositoryError("Failed to list active workflow states") from exc

    async def list_stale(self, threshold: timedelta) -> list[WorkflowState]:
        """Return top-level roots whose *entire subtree* is older than threshold.

        For each top-level root whose own ``updated_at`` is older than the
        cutoff, walks the descendant chain via ``get_active_chain`` and
        computes ``max(updated_at)`` across the subtree.  Only includes
        roots where the entire subtree exceeds the threshold.

        Early-exit: roots whose own ``updated_at`` is fresh are skipped
        entirely, avoiding the descendant walk.
        """
        try:
            cutoff = datetime.now(UTC) - threshold

            # Find top-level roots whose own updated_at is older than cutoff
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.deleted_at.is_(None),
                        WorkflowStateRecord.parent_workflow_id.is_(None),
                        WorkflowStateRecord.updated_at < cutoff,
                    )
                )
                candidate_roots = result.scalars().all()

            stale_roots: list[WorkflowState] = []
            for root_record in candidate_roots:
                root = root_record.to_domain()
                chain = await self.get_active_chain(root.id)
                subtree_max = max(ws.updated_at for ws in chain)
                if subtree_max < cutoff:
                    stale_roots.append(root)

            return stale_roots

        except Exception as exc:
            raise WorkflowRepositoryError("Failed to list stale workflow states") from exc

    # -----------------------------------------------------------------------
    # Composition-aware methods
    # -----------------------------------------------------------------------

    async def get_active_child(self, parent_id: str) -> WorkflowState | None:
        """Return the active child of the given parent, or None."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(WorkflowStateRecord).where(
                        WorkflowStateRecord.parent_workflow_id == parent_id,
                        WorkflowStateRecord.deleted_at.is_(None),
                    )
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise WorkflowRepositoryError(
                f"Failed to get active child for parent={parent_id}"
            ) from exc

    async def get_active_chain(self, root_id: str) -> list[WorkflowState]:
        """Walk the active chain from root downward via ``get_active_child``.

        Returns root-first list.  Empty list if root not found / deleted.
        """
        chain: list[WorkflowState] = []
        current = await self.get(root_id)
        while current is not None:
            chain.append(current)
            current = await self.get_active_child(current.id)
        return chain

    async def abort_cascade(self, root_id: str) -> list[str]:
        """Atomically soft-delete root + every transitive descendant.

        Idempotent: returns ``[]`` if root is already soft-deleted.

        Orphaned children (root deleted but child still active) are NOT
        walked — this is a corruption case that stale cleanup handles
        separately.
        """
        try:
            async with self._session_factory() as db:  # noqa: SIM117
                async with db.begin():
                    ids: list[str] = []
                    # Walk descendants using the open session
                    frontier = [root_id]
                    while frontier:
                        next_frontier: list[str] = []
                        for current_id in frontier:
                            result = await db.execute(
                                select(WorkflowStateRecord).where(
                                    WorkflowStateRecord.id == current_id,
                                    WorkflowStateRecord.deleted_at.is_(None),
                                )
                            )
                            record = result.scalar_one_or_none()
                            if record is None:
                                continue
                            ids.append(record.id)
                            # Find children of this record
                            child_result = await db.execute(
                                select(WorkflowStateRecord).where(
                                    WorkflowStateRecord.parent_workflow_id == record.id,
                                    WorkflowStateRecord.deleted_at.is_(None),
                                )
                            )
                            for child in child_result.scalars().all():
                                next_frontier.append(child.id)
                        frontier = next_frontier

                    if ids:
                        now = datetime.now(UTC)
                        await db.execute(
                            update(WorkflowStateRecord)
                            .where(WorkflowStateRecord.id.in_(ids))
                            .values(deleted_at=now, updated_at=now)
                        )

            return ids

        except Exception as exc:
            raise WorkflowRepositoryError(
                f"Failed to abort cascade for root={root_id}"
            ) from exc

    async def apply_mutation_batch(self, batch: MutationBatch) -> None:
        """Apply a ``MutationBatch`` atomically in a single transaction.

        If any mutation raises, the entire batch rolls back (R13).
        """
        try:
            async with self._session_factory() as db:  # noqa: SIM117
                async with db.begin():
                    for mutation in batch.ordered:
                        if isinstance(mutation, UpdateState):
                            result = await db.execute(
                                select(WorkflowStateRecord).where(
                                    WorkflowStateRecord.id == mutation.layer_id,
                                )
                            )
                            record = result.scalar_one_or_none()
                            if record is not None:
                                record.step_states = json.dumps(mutation.step_states)
                                record.current_step = mutation.current_step
                                record.updated_at = datetime.now(UTC)
                                if mutation.loop_state is not None:
                                    record.loop_state = json.dumps(mutation.loop_state)

                        elif isinstance(mutation, CreateChild):
                            child_record = WorkflowStateRecord(
                                id=mutation.child_id,
                                skill_name=mutation.skill_name,
                                workflow_name=mutation.workflow_name,
                                parent_workflow_id=mutation.parent_id,
                                parent_step_id=mutation.parent_step_id,
                                current_step=None,
                                step_states=json.dumps(mutation.step_states),
                                definition_snapshot=json.dumps(mutation.definition_snapshot),
                                scratchpad_path=mutation.scratchpad_path,
                                deleted_at=None,
                                created_at=datetime.now(UTC),
                                updated_at=datetime.now(UTC),
                            )
                            db.add(child_record)

                        elif isinstance(mutation, SoftDelete):
                            now = datetime.now(UTC)
                            await db.execute(
                                update(WorkflowStateRecord)
                                .where(WorkflowStateRecord.id == mutation.layer_id)
                                .values(deleted_at=now, updated_at=now)
                            )

        except Exception as exc:
            raise WorkflowRepositoryError(
                "Failed to apply mutation batch"
            ) from exc
