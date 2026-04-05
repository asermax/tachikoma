"""Tests for task event classes."""

from unittest.mock import AsyncMock

import pytest

from tachikoma.tasks.events import SessionTaskReady

from .conftest import _make_instance


class TestSessionTaskReady:
    """Tests for SessionTaskReady event."""

    def test_construction(self) -> None:
        """AC: SessionTaskReady event is created with all required fields."""
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
