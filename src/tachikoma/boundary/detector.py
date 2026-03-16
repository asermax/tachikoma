"""Boundary detection for conversation topic shifts.

Uses standalone Opus query with low effort and JSON schema output for fast,
reliable classification. Returns True for continuation, False for topic shift.
"""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.boundary.prompts import (
    BOUNDARY_DETECTION_SYSTEM_PROMPT,
    BOUNDARY_DETECTION_USER_PROMPT,
)

_log = logger.bind(component="boundary")


async def detect_boundary(
    message: str, summary: str, cwd: Path, *, cli_path: str | None = None
) -> bool:
    """Detect whether a message continues the current conversation or starts a new topic.

    **IMPORTANT**: Returns True when the conversation CONTINUES (not when a boundary
    is detected). Returns False only when there is a clear, unambiguous topic shift.

    Uses a standalone Opus query with low effort and JSON schema output for fast,
    reliable classification. This call is independent of the coordinator's SDK session.

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
        model="opus",
        effort="low",
        cwd=cwd,
        cli_path=cli_path,
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

    # Fully consume the query() generator to ensure proper SDK cleanup.
    continues = True
    got_result = False

    async for sdk_message in query(prompt=user_prompt, options=options):
        if isinstance(sdk_message, ResultMessage):
            got_result = True

            if sdk_message.is_error:
                _log.warning(
                    "Boundary detection returned error: err={err}",
                    err=sdk_message.result,
                )
            elif sdk_message.structured_output is not None:
                continues = bool(
                    sdk_message.structured_output.get("continues_conversation", True)
                )
                _log.debug(
                    "Boundary detection result: continues={continues}",
                    continues=continues,
                )
            else:
                _log.warning("Boundary detection returned no structured output")

    if not got_result:
        _log.warning("Boundary detection query produced no ResultMessage")

    return continues
