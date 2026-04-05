"""Task domain model and SQLAlchemy ORM models.

Keeps the ORM models (TaskDefinitionRecord, TaskInstanceRecord) internal to
the persistence layer. Callers work exclusively with frozen dataclasses.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base
from tachikoma.db_utils import ensure_utc

# ---------------------------------------------------------------------------
# Domain types — public API
# ---------------------------------------------------------------------------

TaskStatus = Literal["pending", "running", "completed", "failed"]
TaskType = Literal["session", "background"]
ScheduleType = Literal["cron", "once"]


@dataclass(frozen=True)
class ScheduleConfig:
    """Schedule configuration for a task definition.

    Either cron (recurring) or once (one-shot).
    """

    type: ScheduleType
    expression: str | None = None  # cron expression when type="cron"
    at: datetime | None = None  # target datetime when type="once"

    def to_json(self) -> str:
        """Serialize to JSON string for storage."""
        data: dict[str, str] = {"type": self.type}
        if self.expression is not None:
            data["expression"] = self.expression
        if self.at is not None:
            data["at"] = self.at.isoformat()
        return json.dumps(data)

    @classmethod
    def from_json(cls, json_str: str) -> "ScheduleConfig":
        """Deserialize from JSON string.

        Handles legacy bare ISO datetime strings (treated as one-shot).
        """
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            # Legacy format: bare ISO datetime string stored instead of JSON
            try:
                at = datetime.fromisoformat(json_str)
                if at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                return cls(type="once", at=at)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid schedule: {json_str!r}") from None

        if isinstance(data, str):
            at = datetime.fromisoformat(data)
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)
            return cls(type="once", at=at)

        if not isinstance(data, dict):
            raise ValueError(f"Invalid schedule JSON: expected object, got {type(data).__name__}")

        at = None
        if "at" in data and data["at"] is not None:
            at = datetime.fromisoformat(data["at"])
            if at.tzinfo is None:
                at = at.replace(tzinfo=UTC)

        if "type" not in data:
            raise ValueError(f"Invalid schedule JSON: missing 'type' key in {data!r}")

        return cls(
            type=data["type"],
            expression=data.get("expression"),
            at=at,
        )


@dataclass(frozen=True)
class TaskDefinition:
    """Domain representation of a task definition.

    Returned to all callers; has no SQLAlchemy dependency.
    """

    id: str
    name: str
    schedule: ScheduleConfig
    task_type: TaskType
    prompt: str
    enabled: bool = True
    notify: str | None = None
    last_fired_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class TaskInstance:
    """Domain representation of a task instance.

    definition_id is nullable — transient instances (notifications from
    background task results) have no parent definition.
    """

    id: str
    task_type: TaskType
    status: TaskStatus
    prompt: str
    scheduled_for: datetime
    definition_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: str | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# SQLAlchemy ORM — internal to the persistence layer
# ---------------------------------------------------------------------------


class TaskDefinitionRecord(Base):
    """SQLAlchemy ORM model for the task_definitions table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the TaskDefinition dataclass.
    """

    __tablename__ = "task_definitions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    schedule: Mapped[str] = mapped_column(String, nullable=False)  # JSON string
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(String, nullable=False)
    notify: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def to_domain(self) -> TaskDefinition:
        """Convert ORM record to domain dataclass."""
        return TaskDefinition(
            id=self.id,
            name=self.name,
            schedule=ScheduleConfig.from_json(self.schedule),
            task_type=self.task_type,  # type: ignore[arg-type]
            prompt=self.prompt,
            notify=self.notify,
            enabled=self.enabled,
            last_fired_at=ensure_utc(self.last_fired_at),
            created_at=ensure_utc(self.created_at),
        )


class TaskInstanceRecord(Base):
    """SQLAlchemy ORM model for the task_instances table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the TaskInstance dataclass.
    """

    __tablename__ = "task_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    definition_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("task_definitions.id"), nullable=True
    )
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_task_instances_status", "status"),
        Index("ix_task_instances_task_type", "task_type"),
    )

    def to_domain(self) -> TaskInstance:
        """Convert ORM record to domain dataclass."""
        return TaskInstance(
            id=self.id,
            definition_id=self.definition_id,
            task_type=self.task_type,  # type: ignore[arg-type]
            status=self.status,  # type: ignore[arg-type]
            prompt=self.prompt,
            scheduled_for=ensure_utc(self.scheduled_for),  # type: ignore[arg-type]
            started_at=ensure_utc(self.started_at),
            completed_at=ensure_utc(self.completed_at),
            result=self.result,
            created_at=ensure_utc(self.created_at),
        )
