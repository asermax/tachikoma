"""Tests for the notifications module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bubus import EventBus

from tachikoma.notifications import (
    Notification,
    build_notification_prompt,
    create_notification_server,
    dispatch_notification,
    handle_send_notification,
)


class TestNotification:
    """Tests for the Notification event type."""

    def test_construction_info(self) -> None:
        """AC: Notification event is created with info severity."""
        event = Notification(
            prompt="A background task has completed.",
            source_id="task-123",
            severity="info",
        )

        assert event.prompt == "A background task has completed."
        assert event.source_id == "task-123"
        assert event.severity == "info"

    def test_construction_error(self) -> None:
        """AC: Notification event is created with error severity."""
        event = Notification(
            prompt="A background task has failed.",
            source_id="task-456",
            severity="error",
        )

        assert event.prompt == "A background task has failed."
        assert event.source_id == "task-456"
        assert event.severity == "error"

    def test_defaults(self) -> None:
        """AC: Notification has sensible defaults."""
        event = Notification(prompt="Hello")

        assert event.prompt == "Hello"
        assert event.source_id is None
        assert event.severity == "info"

    def test_severity_literal(self) -> None:
        """AC: severity must be 'info' or 'error'."""
        Notification(prompt="test", severity="info")
        Notification(prompt="test", severity="error")

        with pytest.raises(Exception):
            Notification(prompt="test", severity="warning")


class TestBuildNotificationPrompt:
    """Tests for build_notification_prompt."""

    def test_produces_expected_template(self) -> None:
        """AC: Prompt contains source, timestamp, content, and instruction."""
        before = datetime.now(UTC)
        result = build_notification_prompt(
            "Background task: Daily digest", "Task completed successfully."
        )
        after = datetime.now(UTC)

        assert "--- Notification ---" in result
        assert "Source: Background task: Daily digest" in result
        assert "Time: " in result
        assert "Task completed successfully." in result
        assert "Deliver this notification to the user, keeping your message concise." in result

        # Verify timestamp is within the time window (minute precision)
        time_line = [line for line in result.split("\n") if line.startswith("Time: ")][0]
        timestamp_str = time_line.replace("Time: ", "").replace(" UTC", "")
        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M")
        assert before.replace(second=0, microsecond=0, tzinfo=None) <= timestamp
        assert timestamp <= after.replace(second=0, microsecond=0, tzinfo=None) + __import__(
            "datetime"
        ).timedelta(minutes=1)


class TestDispatchNotification:
    """Tests for dispatch_notification."""

    @pytest.mark.asyncio
    async def test_dispatches_notification_with_correct_fields(self) -> None:
        """AC: dispatch_notification dispatches a Notification event with the right fields."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await dispatch_notification(
            bus,
            source="Background task: My Task",
            content="Task finished!",
            severity="error",
            source_id="inst-789",
        )

        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        assert isinstance(event, Notification)
        assert event.severity == "error"
        assert event.source_id == "inst-789"
        assert "Background task: My Task" in event.prompt
        assert "Task finished!" in event.prompt

    @pytest.mark.asyncio
    async def test_dispatch_with_defaults(self) -> None:
        """AC: dispatch_notification uses info severity and no source_id by default."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await dispatch_notification(bus, source="Test", content="Hello", severity="info")

        event = dispatched_events[0]
        assert event.severity == "info"
        assert event.source_id is None


class TestHandleSendNotification:
    """Tests for the send_notification tool handler."""

    @pytest.mark.asyncio
    async def test_returns_error_for_empty_message(self) -> None:
        """AC: Handler returns error response when message is empty."""
        bus = EventBus()
        bus.dispatch = AsyncMock()

        result = await handle_send_notification(
            message="   ",
            bus=bus,
            source="Test",
            source_id="id-1",
        )

        assert result["is_error"] is True
        assert "empty" in result["content"][0]["text"].lower()
        bus.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_and_returns_success(self) -> None:
        """AC: Handler dispatches Notification and returns success for valid message."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        result = await handle_send_notification(
            message="Progress: 50% complete",
            bus=bus,
            source="Background task: Cleanup",
            source_id="inst-100",
        )

        assert "is_error" not in result
        assert "successfully" in result["content"][0]["text"].lower()

        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        assert isinstance(event, Notification)
        assert event.severity == "info"
        assert event.source_id == "inst-100"
        assert "Progress: 50% complete" in event.prompt


class TestCreateNotificationServer:
    """Tests for the MCP tool server factory."""

    def test_returns_server_config(self) -> None:
        """AC: Factory returns an McpSdkServerConfig with the notification tool."""
        bus = EventBus()
        bus.dispatch = AsyncMock()

        server = create_notification_server(bus, "Test Source", "id-1")

        assert server is not None
