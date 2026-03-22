"""Tests for task event classes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tachikoma.tasks.events import SessionTaskReady, TaskNotification

from .conftest import _make_instance


class TestSessionTaskReady:
    """Tests for SessionTaskReady event."""

    def test_construction(self) -> None:
        """AC: SessionTaskReady event is created with all required fields."""
        now = datetime.now(UTC)
        instance = _make_instance("inst-1", task_type="session", status="pending")
        mock_callback = AsyncMock()

        event = SessionTaskReady(instance=instance, on_complete=mock_callback)

        assert event.instance.id == "inst-1"
        assert event.instance.task_type == "session"
        assert event.instance.status == "pending"
        assert event.on_complete == mock_callback

    def test_on_complete_excluded_from_serialization(self) -> None:
        """AC: on_complete callback is excluded from model serialization."""
        instance = _make_instance("inst-1", task_type="session")
        mock_callback = AsyncMock()

        event = SessionTaskReady(instance=instance, on_complete=mock_callback)

        # Pydantic model_dump should exclude on_complete
        data = event.model_dump()
        assert "on_complete" not in data
        assert "instance" in data

    @pytest.mark.asyncio
    async def test_on_complete_callback(self) -> None:
        """AC: on_complete callback can be invoked."""
        instance = _make_instance("inst-1", task_type="session")
        mock_callback = AsyncMock()

        event = SessionTaskReady(instance=instance, on_complete=mock_callback)

        # Invoke the callback
        await event.on_complete()

        mock_callback.assert_called_once()


class TestTaskNotification:
    """Tests for TaskNotification event."""

    def test_construction_info(self) -> None:
        """AC: TaskNotification event is created with info severity."""
        event = TaskNotification(
            message="Task completed successfully",
            source_task_id="task-123",
            severity="info",
        )

        assert event.message == "Task completed successfully"
        assert event.source_task_id == "task-123"
        assert event.severity == "info"

    def test_construction_error(self) -> None:
        """AC: TaskNotification event is created with error severity."""
        event = TaskNotification(
            message="Task failed: timeout",
            source_task_id="task-456",
            severity="error",
        )

        assert event.message == "Task failed: timeout"
        assert event.source_task_id == "task-456"
        assert event.severity == "error"

    def test_defaults(self) -> None:
        """AC: TaskNotification has sensible defaults."""
        event = TaskNotification(message="Notification")

        assert event.message == "Notification"
        assert event.source_task_id is None
        assert event.severity == "info"  # default

    def test_severity_literal(self) -> None:
        """AC: severity must be 'info' or 'error'."""
        # Valid values
        TaskNotification(message="test", severity="info")
        TaskNotification(message="test", severity="error")

        # Invalid value should raise
        with pytest.raises(Exception):  # Pydantic ValidationError
            TaskNotification(message="test", severity="warning")
