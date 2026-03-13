"""Post-processing pipeline for running processors after conversation end.

Provides a reusable, pluggable pipeline that runs PostProcessor instances
in parallel with error isolation. Used by memory extraction (DLT-008) and
will be extended by future post-processors (DLT-018, DLT-020).
"""

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from loguru import logger

from tachikoma.sessions.model import Session

_log = logger.bind(component="post_processing")


class PostProcessor(ABC):
    """Abstract base class for post-processing handlers.

    Subclasses implement process() to perform their specific extraction
    or update logic. The ABC defines only the interface contract — no
    SDK coupling is inherited.
    """

    @abstractmethod
    async def process(self, session: Session) -> None:
        """Process a closed session.

        Args:
            session: The closed session with sdk_session_id for forking.
        """
        ...


class PostProcessingPipeline:
    """Runs registered PostProcessor instances in parallel with error isolation.

    Usage::

        pipeline = PostProcessingPipeline()
        pipeline.register(EpisodicProcessor(cwd))
        pipeline.register(FactsProcessor(cwd))
        await pipeline.run(session)

    Individual processor failures are logged but don't prevent other
    processors from completing.
    """

    def __init__(self) -> None:
        self._processors: list[PostProcessor] = []
        self._lock = asyncio.Lock()

    def register(self, processor: PostProcessor) -> None:
        """Register a processor to run on pipeline execution."""
        self._processors.append(processor)

    async def run(self, session: Session) -> None:
        """Run all registered processors in parallel.

        Acquires an internal lock to serialize concurrent invocations.
        Each processor runs independently — failures are logged per
        DES-002 but don't propagate.
        """
        async with self._lock:
            results = await asyncio.gather(
                *[p.process(session) for p in self._processors],
                return_exceptions=True,
            )

            for processor, result in zip(self._processors, results, strict=True):
                if isinstance(result, Exception):
                    _log.exception(
                        "Processor failed: processor={name} err={err}",
                        name=processor.__class__.__name__,
                        err=str(result),
                    )


async def fork_and_consume(session: Session, prompt: str, cwd: Path) -> None:
    """Fork the SDK session and consume the agent's response.

    Creates a forked session using the standalone query() function,
    which operates independently of the coordinator's ClaudeSDKClient.
    The forked agent has full workspace access.

    Args:
        session: The session to fork (must have sdk_session_id).
        prompt: The extraction prompt to send to the forked agent.
        cwd: The working directory for the forked agent.

    Raises:
        RuntimeError: If session has no sdk_session_id.
        Propagates: SDK errors from the query() call.
    """
    if session.sdk_session_id is None:
        raise RuntimeError(
            f"Cannot fork session {session.id}: no sdk_session_id available"
        )

    options = ClaudeAgentOptions(
        cwd=cwd,
        resume=session.sdk_session_id,
        fork_session=True,
    )

    # Fully consume the async iterator to ensure the forked session ends cleanly
    async for _ in query(prompt=prompt, options=options):
        pass
