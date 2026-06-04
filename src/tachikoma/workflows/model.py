"""Workflow state domain model and SQLAlchemy ORM model.

Keeps the ORM model (WorkflowStateRecord) internal to the persistence layer.
Callers work exclusively with frozen dataclasses.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base
from tachikoma.db_utils import ensure_utc

# ---------------------------------------------------------------------------
# Domain types — public API
# ---------------------------------------------------------------------------

StepState = Literal["pending", "started", "completed", "skipped"]

STEP_PENDING: StepState = "pending"
STEP_STARTED: StepState = "started"
STEP_COMPLETED: StepState = "completed"
STEP_SKIPPED: StepState = "skipped"


@dataclass(frozen=True)
class WorkflowState:
    """Domain representation of a workflow state.

    Returned to all callers; has no SQLAlchemy dependency.
    """

    id: str
    skill_name: str
    workflow_name: str
    current_step: str | None
    step_states: dict[str, StepState]
    definition_snapshot: list[dict]
    scratchpad_path: str
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    parent_workflow_id: str | None = None
    parent_step_id: str | None = None
    loop_state: dict | None = None


# ---------------------------------------------------------------------------
# SQLAlchemy ORM — internal to the persistence layer
# ---------------------------------------------------------------------------


class WorkflowStateRecord(Base):
    """SQLAlchemy ORM model for the workflow_states table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the WorkflowState dataclass.
    """

    __tablename__ = "workflow_states"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    skill_name: Mapped[str] = mapped_column(String, nullable=False)
    workflow_name: Mapped[str] = mapped_column(String, nullable=False)
    parent_workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_step_id: Mapped[str | None] = mapped_column(String, nullable=True)
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    step_states: Mapped[str] = mapped_column(String, nullable=False)
    definition_snapshot: Mapped[str] = mapped_column(String, nullable=False)
    scratchpad_path: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    loop_state: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_workflow_states_skill_name", "skill_name"),
        Index("ix_workflow_states_workflow_name", "workflow_name"),
        Index("ix_workflow_states_active_lookup", "skill_name", "workflow_name"),
        Index("ix_workflow_states_parent", "parent_workflow_id", "deleted_at"),
    )

    def to_domain(self) -> WorkflowState:
        """Convert ORM record to domain dataclass."""
        return WorkflowState(
            id=self.id,
            skill_name=self.skill_name,
            workflow_name=self.workflow_name,
            current_step=self.current_step,
            step_states=dict(json.loads(self.step_states)),
            definition_snapshot=json.loads(self.definition_snapshot),
            scratchpad_path=self.scratchpad_path,
            deleted_at=ensure_utc(self.deleted_at),
            created_at=ensure_utc(self.created_at),
            updated_at=ensure_utc(self.updated_at),
            parent_workflow_id=self.parent_workflow_id,
            parent_step_id=self.parent_step_id,
            loop_state=json.loads(self.loop_state) if self.loop_state is not None else None,
        )
