"""Per-message post-processing pipeline for running processors after each response.

Provides a reusable, pluggable pipeline that runs MessagePostProcessor instances
in parallel with error isolation. Unlike the session-level PostProcessingPipeline,
this pipeline runs after each agent response with the message context.
"""

import asyncio
from abc import ABC, abstractmethod

from loguru import logger

from tachikoma.sessions.model import Session

_log = logger.bind(component="message_post_processing")


class MessagePostProcessor(ABC):
    """Abstract base class for per-message post-processing handlers.

    Subclasses implement process() to perform their specific logic with access
    to the session and the latest message exchange.
    """

    @abstractmethod
    async def process(
        self, session: Session, user_message: str, agent_response: str
    ) -> None:
        """Process a message exchange.

        Args:
            session: The active session (may have summary updated by other processors).
            user_message: The user's input text.
            agent_response: The agent's response text.
        """
        ...


class MessagePostProcessingPipeline:
    """Runs registered MessagePostProcessor instances in parallel with error isolation.

    Usage::

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(SummaryProcessor(registry, cwd))
        await pipeline.run(session, user_message, agent_response)

    Individual processor failures are logged but don't prevent other
    processors from completing.
    """

    def __init__(self) -> None:
        self._processors: list[MessagePostProcessor] = []
        self._lock = asyncio.Lock()

    def register(self, processor: MessagePostProcessor) -> None:
        """Register a processor to run on pipeline execution.

        Args:
            processor: The processor to register.
        """
        self._processors.append(processor)

    async def run(
        self, session: Session, user_message: str, agent_response: str
    ) -> None:
        """Run all registered processors in parallel.

        Acquires an internal lock to serialize concurrent invocations.
        Individual processor failures are logged per DES-002 but don't
        propagate or prevent other processors from completing.
        """
        async with self._lock:
            if not self._processors:
                return

            results = await asyncio.gather(
                *[
                    p.process(session, user_message, agent_response)
                    for p in self._processors
                ],
                return_exceptions=True,
            )

            for processor, result in zip(self._processors, results, strict=True):
                if isinstance(result, Exception):
                    _log.exception(
                        "Processor failed: processor={name} err={err}",
                        name=processor.__class__.__name__,
                        err=str(result),
                    )
