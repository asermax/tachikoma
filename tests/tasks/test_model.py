"""Unit tests for task domain types and ORM models."""

from datetime import UTC, datetime

import pytest

from tachikoma.tasks.model import (
    ScheduleConfig,
    TaskDefinition,
    TaskDefinitionRecord,
    TaskInstance,
    TaskInstanceRecord,
    _ensure_utc,
)


class TestScheduleConfig:
    """Tests for ScheduleConfig serialization."""

    def test_cron_schedule_to_json(self) -> None:
        """AC: Cron schedule serializes correctly."""
        config = ScheduleConfig(type="cron", expression="0 9 * * *")
        json_str = config.to_json()

        assert '"type": "cron"' in json_str
        assert '"expression": "0 9 * * *"' in json_str

    def test_once_schedule_to_json(self) -> None:
        """AC: One-shot schedule serializes correctly."""
        target = datetime(2026, 3, 22, 10, 0, tzinfo=UTC)
        config = ScheduleConfig(type="once", at=target)
        json_str = config.to_json()

        assert '"type": "once"' in json_str
        assert '"at": "2026-03-22T10:00:00+00:00"' in json_str

    def test_cron_round_trip(self) -> None:
        """AC: Cron schedule round-trips through JSON."""
        original = ScheduleConfig(type="cron", expression="*/5 * * * *")
        json_str = original.to_json()
        restored = ScheduleConfig.from_json(json_str)

        assert restored.type == "cron"
        assert restored.expression == "*/5 * * * *"
        assert restored.at is None

    def test_once_round_trip(self) -> None:
        """AC: One-shot schedule round-trips through JSON."""
        target = datetime(2026, 3, 22, 10, 0, tzinfo=UTC)
        original = ScheduleConfig(type="once", at=target)
        json_str = original.to_json()
        restored = ScheduleConfig.from_json(json_str)

        assert restored.type == "once"
        assert restored.at == target
        assert restored.expression is None

    def test_once_naive_datetime_gets_utc(self) -> None:
        """AC: Naive datetime in JSON gets UTC tzinfo."""
        import json

        # Simulate stored JSON without timezone
        json_str = json.dumps({"type": "once", "at": "2026-03-22T10:00:00"})
        restored = ScheduleConfig.from_json(json_str)

        assert restored.at is not None
        assert restored.at.tzinfo is not None
        assert restored.at.tzinfo == UTC


class TestEnsureUtc:
    """Tests for _ensure_utc helper."""

    def test_none_returns_none(self) -> None:
        """AC: None input returns None."""
        assert _ensure_utc(None) is None

    def test_naive_gets_utc(self) -> None:
        """AC: Naive datetime gets UTC tzinfo."""
        naive = datetime(2026, 3, 22, 10, 0)
        result = _ensure_utc(naive)

        assert result is not None
        assert result.tzinfo == UTC
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 22

    def test_aware_unchanged(self) -> None:
        """AC: Timezone-aware datetime is unchanged."""
        from zoneinfo import ZoneInfo

        aware = datetime(2026, 3, 22, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        result = _ensure_utc(aware)

        assert result is aware  # Same object returned


class TestTaskDefinition:
    """Tests for TaskDefinition dataclass."""

    def test_create_definition(self) -> None:
        """AC: TaskDefinition can be created with all fields."""
        schedule = ScheduleConfig(type="cron", expression="0 9 * * *")
        definition = TaskDefinition(
            id="test-id",
            name="Morning reminder",
            schedule=schedule,
            task_type="session",
            prompt="Remind the user to check emails",
            enabled=True,
            notify=None,
            last_fired_at=None,
            created_at=datetime.now(UTC),
        )

        assert definition.id == "test-id"
        assert definition.name == "Morning reminder"
        assert definition.schedule.type == "cron"
        assert definition.enabled is True

    def test_definition_is_frozen(self) -> None:
        """AC: TaskDefinition is immutable (frozen)."""
        definition = TaskDefinition(
            id="test-id",
            name="Test",
            schedule=ScheduleConfig(type="cron", expression="* * * * *"),
            task_type="session",
            prompt="Test",
        )

        with pytest.raises(AttributeError):
            definition.name = "Changed"  # type: ignore[misc]


class TestTaskInstance:
    """Tests for TaskInstance dataclass."""

    def test_create_instance(self) -> None:
        """AC: TaskInstance can be created with all fields."""
        instance = TaskInstance(
            id="inst-1",
            definition_id="def-1",
            task_type="background",
            status="pending",
            prompt="Process notes",
            scheduled_for=datetime.now(UTC),
        )

        assert instance.id == "inst-1"
        assert instance.definition_id == "def-1"
        assert instance.status == "pending"

    def test_transient_instance_has_null_definition(self) -> None:
        """AC: Transient instances can have null definition_id."""
        instance = TaskInstance(
            id="transient-1",
            definition_id=None,
            task_type="session",
            status="pending",
            prompt="Notification message",
            scheduled_for=datetime.now(UTC),
        )

        assert instance.definition_id is None

    def test_instance_is_frozen(self) -> None:
        """AC: TaskInstance is immutable (frozen)."""
        instance = TaskInstance(
            id="inst-1",
            task_type="session",
            status="pending",
            prompt="Test",
            scheduled_for=datetime.now(UTC),
        )

        with pytest.raises(AttributeError):
            instance.status = "running"  # type: ignore[misc]


class TestORMModels:
    """Tests for ORM model to_domain() conversions."""

    def test_definition_record_to_domain(self) -> None:
        """AC: TaskDefinitionRecord converts to domain correctly."""
        now = datetime.now(UTC)
        record = TaskDefinitionRecord(
            id="def-1",
            name="Test Task",
            schedule='{"type": "cron", "expression": "0 9 * * *"}',
            task_type="session",
            prompt="Test prompt",
            notify="Tell user",
            enabled=True,
            last_fired_at=None,
            created_at=now,
        )

        domain = record.to_domain()

        assert domain.id == "def-1"
        assert domain.name == "Test Task"
        assert domain.schedule.type == "cron"
        assert domain.schedule.expression == "0 9 * * *"
        assert domain.notify == "Tell user"

    def test_instance_record_to_domain(self) -> None:
        """AC: TaskInstanceRecord converts to domain correctly."""
        now = datetime.now(UTC)
        record = TaskInstanceRecord(
            id="inst-1",
            definition_id="def-1",
            task_type="background",
            status="completed",
            prompt="Test prompt",
            scheduled_for=now,
            started_at=now,
            completed_at=now,
            result="Success",
            created_at=now,
        )

        domain = record.to_domain()

        assert domain.id == "inst-1"
        assert domain.definition_id == "def-1"
        assert domain.status == "completed"
        assert domain.result == "Success"
