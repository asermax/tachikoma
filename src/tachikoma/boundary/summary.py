"""Summary processor for rolling conversation summaries.

Generates and updates concise rolling summaries after each agent response.
Uses standalone Opus query with low effort for fast, cost-effective summarization.
"""

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, TextBlock
from loguru import logger

from tachikoma.boundary.prompts import SUMMARY_SYSTEM_PROMPT, SUMMARY_USER_PROMPT
from tachikoma.message_post_processing import MessagePostProcessor
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry

_log = logger.bind(component="boundary")


class SummaryProcessor(MessagePostProcessor):
    """Per-message processor that maintains a rolling conversation summary.

    After each agent response, this processor generates or updates a concise
    summary of the conversation. The summary is stored on the session record
    and used by the boundary detector for topic shift detection.
    """

    def __init__(
        self, registry: SessionRegistry, cwd: Path, cli_path: str | None = None
    ) -> None:
        """Initialize the summary processor.

        Args:
            registry: The session registry for persisting summary updates.
            cwd: The working directory for SDK subprocess calls.
            cli_path: Optional path to the Claude CLI binary.
        """
        self._registry = registry
        self._cwd = cwd
        self._cli_path = cli_path

    async def process(
        self, session: Session, user_message: str, agent_response: str
    ) -> None:
        """Generate or update the rolling conversation summary.

        Args:
            session: The active session (may have previous summary).
            user_message: The user's input text.
            agent_response: The agent's response text.
        """
        # Build the previous summary section
        if session.summary is None:
            previous_summary_section = "No previous summary. This is the first exchange."
        else:
            previous_summary_section = f"Previous summary:\n{session.summary}"

        user_prompt = SUMMARY_USER_PROMPT.format(
            previous_summary_section=previous_summary_section,
            user_message=user_message,
            agent_response=agent_response,
        )

        options = ClaudeAgentOptions(
            model="opus",
            effort="low",
            cwd=self._cwd,
            cli_path=self._cli_path,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            allowed_tools=[],
            permission_mode="bypassPermissions",
        )

        # Collect response text from the assistant
        response_text = ""
        async for sdk_message in query(prompt=user_prompt, options=options):
            if isinstance(sdk_message, AssistantMessage):
                for block in sdk_message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        # Update the session summary if we got a response
        if response_text.strip():
            await self._registry.update_summary(session.id, response_text.strip())
        else:
            _log.warning(
                "Summary processor received empty response: session_id={id}",
                id=session.id,
            )
