"""Notification system for delivering messages to the user.

Provides a generic Notification event, prompt builder, dispatch helper,
and an MCP tool server factory for agent-driven notifications during
background task execution.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from bubus import BaseEvent, EventBus
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel

from tachikoma.buffer.priority import Priority

_log = logger.bind(component="notifications")


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


class Notification(BaseEvent[None]):
    """Event dispatched when a notification should be delivered to the user.

    Channels subscribe to this event and route the prompt through the
    coordinator pipeline for delivery.
    """

    prompt: str

    source_id: str | None = None

    severity: Literal["info", "error"] = "info"

    priority: Priority = Priority.NORMAL

    # When set, this notification is respondable — the main agent should call
    # respond_to_task(task_instance_id=<this id>, response=...) after delivery.
    response_instance_id: str | None = None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_notification_prompt(
    source: str,
    content: str,
    response_instance_id: str | None = None,
) -> str:
    """Build a structured notification prompt.

    Args:
        source: Identifier for the notification source (e.g. "Background task: Daily digest").
        content: The notification message body.
        response_instance_id: When set, appends respondable instructions so the
            main agent knows to route the user's reply via respond_to_task.

    Returns:
        A formatted prompt string with source, timestamp, and content.
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    prompt = f"--- Notification ---\nSource: {source}\nTime: {timestamp}\n\n{content}\n"

    if response_instance_id is not None:
        prompt += (
            "\n"
            "This background task is waiting for user input. Deliver the question above, "
            f'then use respond_to_task(task_instance_id="{response_instance_id}", '
            "response=\"<user's reply>\") to route the user's response back to the task.\n"
        )
    else:
        prompt += "\nDeliver this notification to the user, keeping your message concise.\n"

    return prompt


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------


async def dispatch_notification(
    bus: EventBus,
    source: str,
    content: str,
    severity: Literal["info", "error"],
    source_id: str | None = None,
    priority: Priority = Priority.NORMAL,
    response_instance_id: str | None = None,
) -> None:
    """Build a notification prompt and dispatch it as a Notification event.

    Args:
        bus: The EventBus to dispatch the event on.
        source: Identifier for the notification source.
        content: The notification message body.
        severity: Severity level — "info" for informational, "error" for failures.
        source_id: Optional ID of the originating entity (e.g. task instance ID).
        priority: Delivery priority — defaults to Normal.
        response_instance_id: When set, the notification is respondable — includes
            respond_to_task usage instructions for the main agent.
    """
    prompt = build_notification_prompt(source, content, response_instance_id)
    event = Notification(
        prompt=prompt,
        source_id=source_id,
        severity=severity,
        priority=priority,
        response_instance_id=response_instance_id,
    )

    await bus.dispatch(event)
    _log.info(
        "Dispatched Notification: source_id={source_id}, severity={severity}, priority={priority}",
        source_id=source_id,
        severity=severity,
        priority=priority.name,
    )


# ---------------------------------------------------------------------------
# MCP tool server factory (DES-006)
# ---------------------------------------------------------------------------


@dataclass
class NotificationCycleState:
    """Tracks per-iteration and per-execution state shared between tools and the executor.

    Created per-execution, reset per-iteration (except ``workflow_tool_called``
    which accumulates across iterations). Tools set flags during
    ``receive_response()``; the executor reads them after.
    """

    await_response_requested: bool = False
    workflow_tool_called: bool = False

    def reset(self) -> None:
        self.await_response_requested = False


class SendNotificationArgs(BaseModel):
    message: str
    priority: Literal["urgent", "normal", "low"] = "normal"
    await_response: bool = False


_PRIORITY_MAP: dict[str, Priority] = {
    "urgent": Priority.URGENT,
    "normal": Priority.NORMAL,
    "low": Priority.LOW,
}


async def handle_send_notification(
    message: str,
    bus: EventBus,
    source: str,
    source_id: str,
    priority: str = "normal",
    await_response: bool = False,
    cycle_state: NotificationCycleState | None = None,
) -> dict:
    """Handle the send_notification tool invocation.

    Validates the message is non-empty, dispatches a Notification event,
    and returns a success/error response dict.

    Args:
        message: The notification message to send.
        bus: The EventBus to dispatch on.
        source: The notification source identifier.
        source_id: The originating entity ID.
        priority: Delivery priority — "urgent", "normal", or "low".
        await_response: When true, the notification is respondable and the task
            will transition to waiting for user input. Priority is forced to Urgent.
        cycle_state: Optional shared state for the executor to check after the
            agent's response completes.

    Returns:
        MCP tool response dict.
    """
    if not message.strip():
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "Message cannot be empty."}],
        }

    if await_response:
        if cycle_state is not None:
            cycle_state.await_response_requested = True
        await dispatch_notification(
            bus,
            source,
            message,
            "info",
            source_id,
            priority=Priority.URGENT,
            response_instance_id=source_id,
        )
    else:
        resolved_priority = _PRIORITY_MAP.get(priority, Priority.NORMAL)
        await dispatch_notification(
            bus,
            source,
            message,
            "info",
            source_id,
            priority=resolved_priority,
        )

    return {
        "content": [{"type": "text", "text": "Notification sent successfully."}],
    }


def create_notification_server(
    bus: EventBus,
    source: str,
    source_id: str,
    cycle_state: NotificationCycleState | None = None,
) -> McpSdkServerConfig:
    """Create an MCP server exposing the send_notification tool.

    Follows the DES-006 factory pattern: tool handler is a closure over
    config parameters, with extracted logic in handle_send_notification.

    Args:
        bus: The EventBus to dispatch Notification events on.
        source: Identifier for the notification source.
        source_id: ID of the originating entity.
        cycle_state: Optional shared state for the executor to check after the
            agent's response completes.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "send_notification",
        "Send a notification to the user during background task execution. "
        "Use this to deliver progress updates or results to the user. "
        "The priority parameter controls delivery urgency: "
        "'urgent' for time-sensitive results, 'normal' for standard results (default), "
        "'low' for informational updates that can wait. "
        "Set await_response to true to request user input — this pauses execution "
        "and the user's response will arrive as the next conversation turn. "
        "When await_response is true, priority is forced to urgent.",
        SendNotificationArgs.model_json_schema(),
    )
    async def send_notification(args: dict) -> dict:
        parsed = SendNotificationArgs.model_validate(args)
        return await handle_send_notification(
            parsed.message,
            bus,
            source,
            source_id,
            parsed.priority,
            await_response=parsed.await_response,
            cycle_state=cycle_state,
        )

    return create_sdk_mcp_server(
        name="notifications",
        version="1.0.0",
        tools=[send_notification],
    )
