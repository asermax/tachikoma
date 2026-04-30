"""Tests for the restart notification dispatch logic.

Covers the extracted functions build_back_online_content and
handle_restart_notification that live in tachikoma.updates.rollback.
"""

from unittest.mock import AsyncMock, patch

import pytest
from bubus import EventBus

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import Notification
from tachikoma.updates.rollback import (
    RestartNotification,
    build_back_online_content,
    handle_restart_notification,
)

# ---------------------------------------------------------------------------
# build_back_online_content
# ---------------------------------------------------------------------------


class TestBuildBackOnlineContent:
    def test_update_reason_with_versions(self) -> None:
        """AC1: Update marker produces version-transition message."""
        notification = RestartNotification(
            reason="update",
            previous_version="1.55.0",
            new_version="1.56.0",
            timestamp="2026-04-29T12:00:00+00:00",
        )
        result = build_back_online_content(notification)

        assert "update restart" in result
        assert "upgraded from 1.55.0" in result
        assert "to 1.56.0" in result

    def test_manual_reason(self) -> None:
        """AC2: Manual marker produces generic message without version info."""
        notification = RestartNotification(
            reason="manual",
            previous_version=None,
            new_version=None,
            timestamp="2026-04-29T12:00:00+00:00",
        )
        result = build_back_online_content(notification)

        assert "manual restart" in result
        assert "upgraded" not in result

    def test_update_reason_without_versions(self) -> None:
        """Edge: update reason with None versions falls through to generic message."""
        notification = RestartNotification(
            reason="update",
            previous_version=None,
            new_version=None,
            timestamp="2026-04-29T12:00:00+00:00",
        )
        result = build_back_online_content(notification)

        assert "update restart" in result
        assert "upgraded" not in result


# ---------------------------------------------------------------------------
# handle_restart_notification
# ---------------------------------------------------------------------------


class TestHandleRestartNotification:
    @pytest.mark.asyncio
    async def test_no_dispatch_without_marker(self) -> None:
        """AC5: No marker → no dispatch, no clear."""
        bus = EventBus()
        bus.dispatch = AsyncMock()

        with patch("tachikoma.updates.rollback.clear_restart_notification") as mock_clear:
            await handle_restart_notification(bus, None, rollback_was_dispatched=False)

        bus.dispatch.assert_not_called()
        mock_clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_notification_with_urgent_priority(self) -> None:
        """AC3: Valid marker + no rollback → dispatch with URGENT priority."""
        bus = EventBus()
        dispatched_events: list = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        notification = RestartNotification(
            reason="update",
            previous_version="1.55.0",
            new_version="1.56.0",
            timestamp="2026-04-29T12:00:00+00:00",
        )

        with patch("tachikoma.updates.rollback.clear_restart_notification"):
            await handle_restart_notification(bus, notification, rollback_was_dispatched=False)

        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        assert isinstance(event, Notification)
        assert event.priority == Priority.URGENT
        assert event.source_id == "restart_notification"
        assert event.severity == "info"
        assert "Back Online" in event.prompt
        assert "upgraded from 1.55.0" in event.prompt
        assert "to 1.56.0" in event.prompt

    @pytest.mark.asyncio
    async def test_clears_marker_before_dispatch(self) -> None:
        """AC4: Marker cleared before dispatch (DES-011 consume-once ordering)."""
        bus = EventBus()
        call_order: list[str] = []

        async def capture_dispatch(event):
            call_order.append("dispatch")

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        notification = RestartNotification(
            reason="manual",
            previous_version=None,
            new_version=None,
            timestamp="2026-04-29T12:00:00+00:00",
        )

        with patch(
            "tachikoma.updates.rollback.clear_restart_notification",
            side_effect=lambda: call_order.append("clear"),
        ):
            await handle_restart_notification(bus, notification, rollback_was_dispatched=False)

        assert call_order == ["clear", "dispatch"]

    @pytest.mark.asyncio
    async def test_suppresses_dispatch_after_rollback(self) -> None:
        """AC6: Rollback dispatched → marker cleared, no notification sent."""
        bus = EventBus()
        bus.dispatch = AsyncMock()

        notification = RestartNotification(
            reason="update",
            previous_version="1.55.0",
            new_version="1.56.0",
            timestamp="2026-04-29T12:00:00+00:00",
        )

        with patch("tachikoma.updates.rollback.clear_restart_notification") as mock_clear:
            await handle_restart_notification(bus, notification, rollback_was_dispatched=True)

        mock_clear.assert_called_once()
        bus.dispatch.assert_not_called()
