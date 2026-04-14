"""Last exchange processor for persisting the latest assistant response.

Stores the agent's final response text on the session after each exchange.
Simpler than SummaryProcessor — no SDK call needed, just a direct field update.
"""

from loguru import logger

from tachikoma.message_post_processing import MessagePostProcessor
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry

_log = logger.bind(component="boundary")


class LastExchangeProcessor(MessagePostProcessor):
    """Per-message processor that stores the last assistant response on the session.

    After each agent response, this processor persists the response text to
    session.last_exchange via the session registry. The stored response is
    used by the boundary detector for more accurate session routing decisions.
    """

    def __init__(self, registry: SessionRegistry) -> None:
        """Initialize the last exchange processor.

        Args:
            registry: The session registry for persisting last_exchange updates.
        """
        self._registry = registry

    async def process(self, session: Session, user_message: str, agent_response: str) -> None:
        """Store the agent response as the session's last_exchange.

        Skips the update if the response is empty or whitespace-only,
        preserving any previous value.

        Args:
            session: The active session.
            user_message: The user's input text (unused).
            agent_response: The agent's response text.
        """
        if not agent_response.strip():
            _log.debug(
                "Skipping last_exchange update: empty response session_id={id}",
                id=session.id[:8],
            )
            return

        await self._registry.update_last_exchange(session.id, agent_response)
