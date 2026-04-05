"""Notification system for delivering messages to the user.

Provides a generic Notification event, prompt builder, dispatch helper,
and an MCP tool server factory for agent-driven notifications during
background task execution.
"""

from datetime import UTC, datetime
from typing import Literal

from bubus import BaseEvent, EventBus
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel

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


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_notification_prompt(source: str, content: str) -> str:
    """Build a structured notification prompt.

    Args:
        source: Identifier for the notification source (e.g. "Background task: Daily digest").
        content: The notification message body.

    Returns:
        A formatted prompt string with source, timestamp, and content.
    """
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    return (
        "--- Notification ---\n"
        f"Source: {source}\n"
        f"Time: {timestamp}\n"
        "\n"
        f"{content}\n"
        "\n"
        "Deliver this notification to the user, keeping your message concise."
    )


# ---------------------------------------------------------------------------
# Dispatch helper
# ---------------------------------------------------------------------------


async def dispatch_notification(
    bus: EventBus,
    source: str,
    content: str,
    severity: Literal["info", "error"],
    source_id: str | None = None,
) -> None:
    """Build a notification prompt and dispatch it as a Notification event.

    Args:
        bus: The EventBus to dispatch the event on.
        source: Identifier for the notification source.
        content: The notification message body.
        severity: Severity level — "info" for informational, "error" for failures.
        source_id: Optional ID of the originating entity (e.g. task instance ID).
    """
    prompt = build_notification_prompt(source, content)
    event = Notification(
        prompt=prompt,
        source_id=source_id,
        severity=severity,
    )

    await bus.dispatch(event)
    _log.info(
        "Dispatched Notification: source_id={source_id}, severity={severity}",
        source_id=source_id,
        severity=severity,
    )


# ---------------------------------------------------------------------------
# MCP tool server factory (DES-006)
# ---------------------------------------------------------------------------


class SendNotificationArgs(BaseModel):
    message: str


async def handle_send_notification(
    message: str,
    bus: EventBus,
    source: str,
    source_id: str,
) -> dict:
    """Handle the send_notification tool invocation.

    Validates the message is non-empty, dispatches a Notification event,
    and returns a success/error response dict.

    Args:
        message: The notification message to send.
        bus: The EventBus to dispatch on.
        source: The notification source identifier.
        source_id: The originating entity ID.

    Returns:
        MCP tool response dict.
    """
    if not message.strip():
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "Message cannot be empty."}],
        }

    await dispatch_notification(bus, source, message, "info", source_id)

    return {
        "content": [{"type": "text", "text": "Notification sent successfully."}],
    }


def create_notification_server(
    bus: EventBus,
    source: str,
    source_id: str,
) -> McpSdkServerConfig:
    """Create an MCP server exposing the send_notification tool.

    Follows the DES-006 factory pattern: tool handler is a closure over
    config parameters, with extracted logic in handle_send_notification.

    Args:
        bus: The EventBus to dispatch Notification events on.
        source: Identifier for the notification source.
        source_id: ID of the originating entity.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "send_notification",
        "Send a notification to the user during background task execution. "
        "Use this to deliver progress updates or results to the user.",
        SendNotificationArgs.model_json_schema(),
    )
    async def send_notification(args: dict) -> dict:
        parsed = SendNotificationArgs.model_validate(args)
        return await handle_send_notification(
            parsed.message,
            bus,
            source,
            source_id,
        )

    return create_sdk_mcp_server(
        name="notifications",
        version="1.0.0",
        tools=[send_notification],
    )
