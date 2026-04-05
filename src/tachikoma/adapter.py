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
from loguru import logger

from tachikoma.events import AgentEvent, Error, Result, TextChunk, ToolActivity

_log = logger.bind(component="adapter")

NON_RECOVERABLE_ERRORS = frozenset({"authentication_failed", "billing_error"})


def sanitize_text(text: str) -> str:
    """Strip characters that cannot be represented in valid UTF-8.

    The SDK CLI subprocess occasionally produces text containing invalid surrogate
    code points (U+D800–U+DFFF) or other unencodable characters. These cause
    encoding failures in downstream consumers (Telegram API, post-processing pipeline).

    The encode/decode roundtrip preserves all valid Unicode while silently removing
    anything that cannot be encoded as UTF-8.
    """
    sanitized = text.encode("utf-8", errors="ignore").decode("utf-8")

    if len(sanitized) != len(text):
        _log.debug(
            "Sanitized text: removed {removed} unencodable characters",
            removed=len(text) - len(sanitized),
        )

    return sanitized


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
        return [Error(message=sanitize_text(message.error), recoverable=recoverable)]

    events: list[AgentEvent] = []

    for block in message.content:
        if isinstance(block, TextBlock):
            events.append(TextChunk(text=sanitize_text(block.text)))
        elif isinstance(block, ToolUseBlock):
            events.append(ToolActivity(tool_name=block.name, tool_input=block.input))

    return events


def _adapt_result(message: ResultMessage) -> list[AgentEvent]:
    if message.is_error:
        return [Error(message=sanitize_text(message.result or "Unknown error"), recoverable=False)]

    return [
        Result(
            session_id=message.session_id,
            total_cost_usd=message.total_cost_usd,
            usage=message.usage,
        )
    ]
