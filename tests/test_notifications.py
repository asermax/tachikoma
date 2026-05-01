"""Tests for the notifications module."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from bubus import EventBus
from pydantic import ValidationError

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import (
    Notification,
    SendNotificationArgs,
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
        assert event.priority == Priority.NORMAL

    def test_priority_default_is_normal(self) -> None:
        """AC (R7): Notification priority defaults to Normal."""
        event = Notification(prompt="test")

        assert event.priority == Priority.NORMAL

    def test_priority_explicit_urgent(self) -> None:
        """AC (R7): Notification accepts explicit Urgent priority."""
        event = Notification(prompt="urgent!", priority=Priority.URGENT)

        assert event.priority == Priority.URGENT

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
        assert event.priority == Priority.NORMAL

    @pytest.mark.asyncio
    async def test_dispatch_with_explicit_priority(self) -> None:
        """AC (R7): dispatch_notification passes priority to Notification."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await dispatch_notification(
            bus,
            source="Test",
            content="Hello",
            severity="info",
            priority=Priority.URGENT,
        )

        event = dispatched_events[0]
        assert event.priority == Priority.URGENT


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

    @pytest.mark.asyncio
    async def test_dispatches_with_default_normal_priority(self) -> None:
        """AC (R8): Default priority is Normal."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await handle_send_notification(
            message="Done",
            bus=bus,
            source="Test",
            source_id="id-1",
        )

        assert dispatched_events[0].priority == Priority.NORMAL

    @pytest.mark.asyncio
    async def test_dispatches_with_explicit_urgent_priority(self) -> None:
        """AC (R8): Urgent priority is mapped correctly."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await handle_send_notification(
            message="Critical!",
            bus=bus,
            source="Test",
            source_id="id-1",
            priority="urgent",
        )

        assert dispatched_events[0].priority == Priority.URGENT

    @pytest.mark.asyncio
    async def test_dispatches_with_low_priority(self) -> None:
        """AC (R8): Low priority is mapped correctly."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await handle_send_notification(
            message="FYI",
            bus=bus,
            source="Test",
            source_id="id-1",
            priority="low",
        )

        assert dispatched_events[0].priority == Priority.LOW


class TestSendNotificationArgs:
    """Tests for SendNotificationArgs priority field (R8)."""

    def test_default_priority_is_normal(self) -> None:
        parsed = SendNotificationArgs(message="test")

        assert parsed.priority == "normal"

    def test_accepts_all_priority_levels(self) -> None:
        for level in ("urgent", "normal", "low"):
            parsed = SendNotificationArgs(message="test", priority=level)
            assert parsed.priority == level

    def test_rejects_invalid_priority(self) -> None:
        with pytest.raises(ValidationError):
            SendNotificationArgs.model_validate({"message": "test", "priority": "critical"})


class TestCreateNotificationServer:
    """Tests for the MCP tool server factory."""

    def test_returns_server_config(self) -> None:
        """AC: Factory returns an McpSdkServerConfig with the notification tool."""
        bus = EventBus()
        bus.dispatch = AsyncMock()

        server = create_notification_server(bus, "Test Source", "id-1")

        assert server is not None


class TestRespondableNotification:
    """Tests for respondable notification behavior (R2, R2.1)."""

    def test_prompt_includes_respondable_suffix_when_id_provided(self) -> None:
        """AC: Prompt contains respond_to_task instructions when response_instance_id is set."""
        result = build_notification_prompt(
            "Background task: Daily digest",
            "Should I commit these changes?",
            response_instance_id="inst-abc123",
        )

        assert "respond_to_task" in result
        assert "inst-abc123" in result
        assert "waiting for user input" in result
        assert "Should I commit these changes?" in result
        assert "Background task: Daily digest" in result

    def test_prompt_excludes_respondable_suffix_when_id_absent(self) -> None:
        """AC: Standard notifications have no respond_to_task instructions."""
        result = build_notification_prompt(
            "Background task: Daily digest",
            "Task completed successfully.",
        )

        assert "respond_to_task" not in result
        assert "waiting for user input" not in result
        assert "Deliver this notification to the user, keeping your message concise." in result

    @pytest.mark.asyncio
    async def test_dispatch_passes_response_instance_id_through(self) -> None:
        """AC: dispatch_notification threads response_instance_id to both prompt and event."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await dispatch_notification(
            bus,
            source="Background task: Test",
            content="Should I proceed?",
            severity="info",
            source_id="inst-1",
            priority=Priority.URGENT,
            response_instance_id="inst-1",
        )

        event = dispatched_events[0]
        assert event.response_instance_id == "inst-1"
        assert "respond_to_task" in event.prompt
        assert "inst-1" in event.prompt
        assert event.priority == Priority.URGENT

    @pytest.mark.asyncio
    async def test_dispatch_without_response_instance_id_is_non_respondable(self) -> None:
        """AC: dispatch_notification without response_instance_id produces non-respondable event."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await dispatch_notification(
            bus,
            source="Background task: Test",
            content="Task failed!",
            severity="error",
            source_id="inst-1",
        )

        event = dispatched_events[0]
        assert event.response_instance_id is None
        assert "respond_to_task" not in event.prompt
