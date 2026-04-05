"""Boundary detection for conversation topic shifts.

Uses standalone Opus query with low effort and JSON schema output for fast,
reliable classification. Returns BoundaryResult with continuation status and
optional session resumption match.
"""

from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import ResultMessage
from loguru import logger

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.boundary.prompts import (
    BOUNDARY_DETECTION_SYSTEM_PROMPT,
    BOUNDARY_DETECTION_USER_PROMPT,
    CANDIDATES_ONLY_SYSTEM_PROMPT,
    CANDIDATES_ONLY_USER_PROMPT,
    CANDIDATES_SECTION_TEMPLATE,
)

_log = logger.bind(component="boundary")


@dataclass(frozen=True)
class SessionCandidate:
    """A candidate session for potential resumption.

    Attributes:
        id: The session ID (used to identify the session for resumption).
        summary: The conversation summary (used for topic matching).
    """

    id: str
    summary: str


@dataclass(frozen=True)
class BoundaryResult:
    """Result of boundary detection with optional session resumption.

    Attributes:
        continues: True if the message continues the current conversation,
            False if it's a new topic.
        resume_session_id: If a topic shift is detected and a matching candidate
            session is found, this contains the session ID to resume. None otherwise.
    """

    continues: bool
    resume_session_id: str | None = None


async def detect_boundary(
    message: str,
    summary: str | None,
    agent_defaults: AgentDefaults,
    *,
    candidates: list[SessionCandidate] | None = None,
) -> BoundaryResult:
    """Detect whether a message continues the current conversation or starts a new topic.

    **IMPORTANT**: Returns BoundaryResult where continues=True means the conversation
    CONTINUES (not when a boundary is detected). continues=False only when there is
    a clear, unambiguous topic shift.

    When a topic shift is detected and candidate sessions are provided, the detector
    may return a resume_session_id if a matching previous session is found.

    When summary is None, operates in candidates-only mode — comparing the message
    solely against candidate session summaries without a current-topic baseline.
    In this mode, continues is always forced to False.

    Uses a standalone Opus query with low effort and JSON schema output for fast,
    reliable classification. This call is independent of the coordinator's SDK session.

    Args:
        message: The incoming user message text.
        summary: The current session's rolling conversation summary, or None for
            candidates-only mode (startup matching).
        agent_defaults: Common SDK options (cwd, cli_path, env).
        candidates: Optional list of candidate sessions for resumption matching.
            Each candidate has an ID and summary for topic matching.

    Returns:
        BoundaryResult with:
        - continues: True if the message continues the conversation, False if new topic.
          Always False in candidates-only mode.
        - resume_session_id: If a topic shift and matching candidate found, the ID to resume.

    Raises:
        Propagates: SDK errors from the query() call. The coordinator handles
            error wrapping with fail-open behavior.
    """
    # Defense in depth for tool-less agents (see DES-007 "Disabling Tools"):
    # 1. Default permission mode — headless query() has no can_use_tool callback,
    #    so any tool permission request raises an exception.
    # 2. allowed_tools=[] — documents intent. Currently a no-op due to an SDK bug
    #    (empty list is falsy, so --allowedTools is never passed to CLI).
    # 3. max_turns=3 — hard limit prevents runaway execution.

    # Select prompt templates based on mode
    if summary is None:
        # Candidates-only mode: startup matching with no current conversation
        system_prompt = CANDIDATES_ONLY_SYSTEM_PROMPT
        user_prompt = CANDIDATES_ONLY_USER_PROMPT.format(
            message=message,
            candidates=(
                _format_candidates(candidates)
                if candidates
                else "No previous sessions available."
            ),
        )
    else:
        # Normal mode: compare against current conversation summary
        system_prompt = BOUNDARY_DETECTION_SYSTEM_PROMPT
        user_prompt = BOUNDARY_DETECTION_USER_PROMPT.format(summary=summary, message=message)

        if candidates:
            candidates_text = CANDIDATES_SECTION_TEMPLATE.format(
                candidates=_format_candidates(candidates)
            )
            user_prompt = f"{user_prompt}\n\n{candidates_text}"

    options = ClaudeAgentOptions(
        model=agent_defaults.model,
        effort="low",
        max_turns=3,
        cwd=agent_defaults.cwd,
        cli_path=agent_defaults.cli_path,
        env=agent_defaults.env,
        system_prompt=system_prompt,
        allowed_tools=[],
        output_format={
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {
                    "continues_conversation": {"type": "boolean"},
                    "resume_session_id": {"type": ["string", "null"]},
                },
                "required": ["continues_conversation", "resume_session_id"],
                "additionalProperties": False,
            },
        },
    )

    # Fully consume the query() generator to ensure proper SDK cleanup.
    # Default: fail-open (continues=True for normal mode, False for candidates-only)
    default_continues = summary is not None
    result = BoundaryResult(continues=default_continues)
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
                    sdk_message.structured_output.get(
                        "continues_conversation", default_continues
                    )
                )
                resume_id = sdk_message.structured_output.get("resume_session_id")

                if not isinstance(resume_id, str) or resume_id == "":
                    resume_id = None

                # Candidates-only mode: force continues=False
                if summary is None:
                    continues = False

                result = BoundaryResult(continues=continues, resume_session_id=resume_id)

                _log.debug(
                    "Boundary detection result: continues={continues} resume_id={resume_id}",
                    continues=continues,
                    resume_id=resume_id,
                )
            else:
                _log.warning("Boundary detection returned no structured output")

    if not got_result:
        _log.warning("Boundary detection query produced no ResultMessage")

    return result


def _format_candidates(candidates: list[SessionCandidate]) -> str:
    """Format candidates list for the prompt.

    Args:
        candidates: List of session candidates with ID and summary.

    Returns:
        Formatted string with numbered list of candidates.
    """
    lines = []
    for i, candidate in enumerate(candidates, start=1):
        lines.append(f"{i}. Session ID: {candidate.id}")
        lines.append(f"   Summary: {candidate.summary}")

    return "\n".join(lines)
