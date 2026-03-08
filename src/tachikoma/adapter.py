"""Message adapter: transforms SDK Message objects into AgentEvent domain types.

This is the only module that imports SDK message types, keeping the rest of the
application decoupled from SDK internals.
"""

from typing import Any

from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)

from tachikoma.events import AgentEvent, Error, Result, TextChunk, ToolActivity

NON_RECOVERABLE_ERRORS = frozenset({"authentication_failed", "billing_error"})


def adapt(message: Any) -> list[AgentEvent]:
    """Map a single SDK Message to zero or more AgentEvents.

    Returns an empty list for message types that channels don't need
    (UserMessage, SystemMessage, and any unknown types).
    """
    if isinstance(message, AssistantMessage):
        return _adapt_assistant(message)

    if isinstance(message, ResultMessage):
        return _adapt_result(message)

    if isinstance(message, (UserMessage, SystemMessage)):
        return []

    return []


def _adapt_assistant(message: AssistantMessage) -> list[AgentEvent]:
    if message.error is not None:
        recoverable = message.error not in NON_RECOVERABLE_ERRORS
        return [Error(message=message.error, recoverable=recoverable)]

    events: list[AgentEvent] = []

    for block in message.content:
        if isinstance(block, TextBlock):
            events.append(TextChunk(text=block.text))
        elif isinstance(block, ToolUseBlock):
            events.append(
                ToolActivity(tool_name=block.name, tool_input=block.input, result="")
            )

    return events


def _adapt_result(message: ResultMessage) -> list[AgentEvent]:
    if message.is_error:
        return [Error(message=message.result or "Unknown error", recoverable=False)]

    return [
        Result(
            session_id=message.session_id,
            total_cost_usd=message.total_cost_usd,
            usage=message.usage,
        )
    ]
