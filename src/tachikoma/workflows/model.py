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


# ---------------------------------------------------------------------------
# Helper functions for JSON serialization/deserialization
# ---------------------------------------------------------------------------


def _serialize_step_states(step_states: dict[str, StepState]) -> str:
    """Serialize step states dict to JSON string."""
    return json.dumps(step_states)


def _deserialize_step_states(json_str: str) -> dict[str, StepState]:
    """Deserialize step states from JSON string."""
    data = json.loads(json_str)
    return dict(data)


def _serialize_definition_snapshot(definition_snapshot: list[dict]) -> str:
    """Serialize definition snapshot to JSON string."""
    return json.dumps(definition_snapshot)


def _deserialize_definition_snapshot(json_str: str) -> list[dict]:
    """Deserialize definition snapshot from JSON string."""
    return json.loads(json_str)


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
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    step_states: Mapped[str] = mapped_column(String, nullable=False)
    definition_snapshot: Mapped[str] = mapped_column(String, nullable=False)
    scratchpad_path: Mapped[str] = mapped_column(String, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_workflow_states_skill_name", "skill_name"),
        Index("ix_workflow_states_workflow_name", "workflow_name"),
    )

    def to_domain(self) -> WorkflowState:
        """Convert ORM record to domain dataclass."""
        return WorkflowState(
            id=self.id,
            skill_name=self.skill_name,
            workflow_name=self.workflow_name,
            current_step=self.current_step,
            step_states=_deserialize_step_states(self.step_states),
            definition_snapshot=_deserialize_definition_snapshot(self.definition_snapshot),
            scratchpad_path=self.scratchpad_path,
            deleted_at=ensure_utc(self.deleted_at),
            created_at=ensure_utc(self.created_at),  # type: ignore[arg-type]
            updated_at=ensure_utc(self.updated_at),  # type: ignore[arg-type]
        )
