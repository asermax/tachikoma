"""Coordinator: wraps ClaudeSDKClient and exposes a channel-facing async iterator API.

Channels call send_message() and consume the resulting AsyncIterator[AgentEvent].
The coordinator manages the SDK client lifecycle and transforms SDK messages into
domain events via the message adapter.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from types import TracebackType

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError, ProcessError
from loguru import logger

from tachikoma.adapter import adapt
from tachikoma.events import AgentEvent, Error, Result
from tachikoma.sessions.registry import SessionRegistry

_log = logger.bind(component="coordinator")


def _derive_transcript_path(sdk_session_id: str, cwd: Path | None) -> str:
    """Compute the Claude SDK transcript file path from an SDK session ID.

    Follows the Claude SDK convention:
        ~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl

    where <sanitized-cwd> replaces "/" with "-" and strips the leading "-".

    Isolated here so that if the SDK changes its transcript storage format,
    only this one function needs updating.
    """
    effective_cwd = cwd or Path.cwd()
    sanitized = str(effective_cwd).replace("/", "-").lstrip("-")
    return str(Path.home() / ".claude" / "projects" / sanitized / f"{sdk_session_id}.jsonl")


class Coordinator:
    """Programmatic entry point for the agent.

    Usage::

        async with Coordinator(allowed_tools=["Read", "Glob", "Grep"]) as coord:
            async for event in coord.send_message("hello"):
                ...
    """

    def __init__(
        self,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        cwd: Path | None = None,
        registry: SessionRegistry | None = None,
    ) -> None:
        self._options = ClaudeAgentOptions(
            allowed_tools=allowed_tools or [],
            model=model,
            cwd=cwd,
        )
        self._cwd = cwd
        self._client: ClaudeSDKClient | None = None
        self._registry = registry

    async def __aenter__(self) -> "Coordinator":
        self._client = ClaudeSDKClient(self._options)
        await self._client.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Close the active session on clean shutdown before disconnecting
        if self._registry is not None:
            active = await self._registry.get_active_session()

            if active is not None:
                try:
                    await self._registry.close_session(active.id)
                except Exception as exc:
                    _log.exception("Failed to close session on shutdown: err={err}", err=str(exc))

        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        """Send a user message and yield AgentEvents as the agent responds."""
        if self._client is None:
            raise RuntimeError("Coordinator is not connected. Use as an async context manager.")

        # Create a session if this is the first message in a new conversation
        active = None

        if self._registry is not None:
            try:
                active = await self._registry.get_active_session()

                if active is None:
                    active = await self._registry.create_session()

            except Exception as exc:
                # Session tracking failures are logged but never crash the conversation
                _log.exception("Failed to create session: err={err}", err=str(exc))

        await self._client.query(text)

        try:
            async for sdk_message in self._client.receive_messages():
                done = False

                for event in adapt(sdk_message):
                    yield event

                    # Populate SDK session metadata on the first Result event
                    if (
                        isinstance(event, Result)
                        and self._registry is not None
                        and active is not None
                        and event.session_id
                    ):
                        try:
                            transcript_path = _derive_transcript_path(
                                event.session_id, self._cwd
                            )
                            await self._registry.update_metadata(
                                session_id=active.id,
                                sdk_session_id=event.session_id,
                                transcript_path=transcript_path,
                            )
                        except Exception as exc:
                            _log.exception(
                                "Failed to update session metadata: err={err}", err=str(exc)
                            )

                    done = done or isinstance(event, Result)

                if done:
                    break

        except (CLIConnectionError, ProcessError) as exc:
            yield Error(message=str(exc), recoverable=True)

    async def interrupt(self) -> None:
        """Interrupt the current agent response."""
        if self._client is not None:
            await self._client.interrupt()
