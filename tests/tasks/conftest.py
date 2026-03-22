"""Shared fixtures for task tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tachikoma.database import Database
from tachikoma.tasks.model import (
    ScheduleConfig,
    TaskDefinition,
    TaskInstance,
)
from tachikoma.tasks.repository import TaskRepository


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _make_definition(
    definition_id: str = "test-def",
    name: str = "Test Task",
    schedule: ScheduleConfig | None = None,
    task_type: str = "session",
    prompt: str = "Test prompt",
    enabled: bool = True,
    notify: str | None = None,
    last_fired_at: datetime | None = None,
) -> TaskDefinition:
    """Create a TaskDefinition with sensible defaults."""
    return TaskDefinition(
        id=definition_id,
        name=name,
        schedule=schedule or ScheduleConfig(type="cron", expression="0 9 * * *"),
        task_type=task_type,  # type: ignore[arg-type]
        prompt=prompt,
        enabled=enabled,
        notify=notify,
        last_fired_at=last_fired_at,
        created_at=_utcnow(),
    )


def _make_instance(
    instance_id: str = "test-inst",
    definition_id: str | None = "test-def",
    task_type: str = "session",
    status: str = "pending",
    prompt: str = "Test prompt",
    scheduled_for: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    result: str | None = None,
) -> TaskInstance:
    """Create a TaskInstance with sensible defaults."""
    return TaskInstance(
        id=instance_id,
        definition_id=definition_id,
        task_type=task_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        prompt=prompt,
        scheduled_for=scheduled_for or _utcnow(),
        started_at=started_at,
        completed_at=completed_at,
        result=result,
        created_at=_utcnow(),
    )


@pytest.fixture
async def repo(tmp_path: Path) -> TaskRepository:
    """Initialized TaskRepository backed by a temp SQLite file."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield TaskRepository(database.session_factory)
    await database.close()
