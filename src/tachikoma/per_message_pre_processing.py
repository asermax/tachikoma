"""Per-message pre-processing pipeline for context evaluation on every message.

Provides a reusable, pluggable pipeline that runs MessageContextProvider instances
in parallel with error isolation on every message, unlike the session-gated
PreProcessingPipeline which only runs on the first message of a new session.

Providers receive the session's existing context entries, enabling them to
determine what's already loaded and avoid redundant work.
"""

import asyncio
from abc import ABC, abstractmethod

from loguru import logger

from tachikoma.events import StatusCallback
from tachikoma.pre_processing import ContextResult, _humanize_provider_name
from tachikoma.sessions.model import SessionContextEntry

_log = logger.bind(component="per_message_pre_processing")


class MessageContextProvider(ABC):
    """Abstract base class for per-message context providers.

    Standalone ABC (not extending ContextProvider) since per-message providers
    return a list of ContextResults and accept existing_entries, which is
    incompatible with the base ContextProvider signature.
    """

    @abstractmethod
    async def provide(
        self,
        message: str,
        *,
        existing_entries: list[SessionContextEntry] | None = None,
        sdk_session_id: str | None = None,
    ) -> list[ContextResult] | None:
        """Provide context relevant to the user message.

        Args:
            message: The user's message text.
            existing_entries: The session's current context entries.
            sdk_session_id: The current SDK session ID, if available.
                Providers can use this to fork the session for conversation context.

        Returns:
            A list of ContextResult instances, or None if no relevant context.
        """
        ...

    def status_message(self) -> str:
        """Short message describing what this provider does while it runs.

        Emitted by the pipeline as a ``Status`` AgentEvent before ``provide()``
        is awaited. Subclasses should override with a user-facing description.
        """
        return _humanize_provider_name(self.__class__.__name__)


class MessagePreProcessingPipeline:
    """Runs registered MessageContextProvider instances in parallel with error isolation.

    Usage::

        pipeline = MessagePreProcessingPipeline()
        pipeline.register(SkillsContextProvider(agent_defaults, registry))
        results = await pipeline.run(message, existing_entries=entries)

    Individual provider failures are logged but don't prevent other
    providers from completing.
    """

    def __init__(self) -> None:
        self._providers: list[MessageContextProvider] = []
        self._lock = asyncio.Lock()

    def register(self, provider: MessageContextProvider) -> None:
        """Register a provider to run on pipeline execution.

        Args:
            provider: The provider to register.
        """
        self._providers.append(provider)

    async def run(
        self,
        message: str,
        *,
        existing_entries: list[SessionContextEntry] | None = None,
        sdk_session_id: str | None = None,
        on_status: StatusCallback | None = None,
    ) -> list[ContextResult]:
        """Run all registered providers in parallel.

        Acquires an internal lock to serialize concurrent invocations.
        Individual provider failures are logged per DES-002 but don't
        propagate or prevent other providers from completing.

        Args:
            message: The user's message text.
            existing_entries: The session's current context entries.
            sdk_session_id: The current SDK session ID, if available.
            on_status: Optional async callback. If provided, the pipeline
                emits each provider's ``status_message()`` immediately before
                awaiting its ``provide()`` call.

        Returns:
            List of successful, non-None ContextResult instances (flattened from lists).
        """
        async with self._lock:
            if not self._providers:
                return []

            entries = existing_entries or []

            names = [p.__class__.__name__ for p in self._providers]
            _log.info("Pipeline started: providers={names}", names=names)

            async def _run_one(
                provider: MessageContextProvider,
            ) -> list[ContextResult] | ContextResult | None:
                if on_status is not None:
                    await on_status(provider.status_message())
                return await provider.provide(
                    message,
                    existing_entries=entries,
                    sdk_session_id=sdk_session_id,
                )

            results = await asyncio.gather(
                *[_run_one(p) for p in self._providers],
                return_exceptions=True,
            )

            successful: list[ContextResult] = []

            for provider, result in zip(self._providers, results, strict=True):
                if isinstance(result, Exception):
                    _log.exception(
                        "Provider failed: provider={name} err={err}",
                        name=provider.__class__.__name__,
                        err=str(result),
                    )
                elif result is not None:
                    # Flatten list results
                    if isinstance(result, list):
                        successful.extend(result)
                    else:
                        successful.append(result)

            _log.info("Pipeline completed: results={count}", count=len(successful))
            return successful
