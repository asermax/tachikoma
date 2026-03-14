"""Boundary detection for conversation topic shifts.

Uses standalone Haiku query with JSON schema output for fast, reliable
classification. Returns True for continuation, False for topic shift.
"""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage

from tachikoma.boundary.prompts import (
    BOUNDARY_DETECTION_SYSTEM_PROMPT,
    BOUNDARY_DETECTION_USER_PROMPT,
)


async def detect_boundary(message: str, summary: str, cwd: Path) -> bool:
    """Detect whether a message continues the current conversation or starts a new topic.

    **IMPORTANT**: Returns True when the conversation CONTINUES (not when a boundary
    is detected). Returns False only when there is a clear, unambiguous topic shift.

    Uses a standalone Haiku query with JSON schema output for fast, reliable
    classification. This call is independent of the coordinator's SDK session.

    Args:
        message: The incoming user message text.
        summary: The current session's rolling conversation summary.
        cwd: The working directory for the SDK subprocess.

    Returns:
        True if the message continues the conversation, False if it's a new topic.

    Raises:
        Propagates: SDK errors from the query() call. The coordinator handles
            error wrapping with fail-open behavior.
    """
    options = ClaudeAgentOptions(
        model="haiku",
        cwd=cwd,
        system_prompt=BOUNDARY_DETECTION_SYSTEM_PROMPT,
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "continues_conversation": {"type": "boolean"},
                },
                "required": ["continues_conversation"],
                "additionalProperties": False,
            },
        },
        allowed_tools=[],
        permission_mode="bypassPermissions",
    )

    user_prompt = BOUNDARY_DETECTION_USER_PROMPT.format(
        summary=summary, message=message
    )

    async for sdk_message in query(prompt=user_prompt, options=options):
        if isinstance(sdk_message, ResultMessage):
            if sdk_message.structured_output is not None:
                result = sdk_message.structured_output
                return bool(result.get("continues_conversation", True))

            # Fallback: if no structured output, default to continuation
            return True

    # If we never got a ResultMessage, default to continuation
    return True
