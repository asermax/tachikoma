"""Coordinator: wraps ClaudeSDKClient and exposes a channel-facing async iterator API.

Channels call send_message() and consume the resulting AsyncIterator[AgentEvent].
The coordinator manages the SDK client lifecycle and transforms SDK messages into
domain events via the message adapter.
"""

from collections.abc import AsyncIterator
from types import TracebackType

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, CLIConnectionError, ProcessError

from tachikoma.adapter import adapt
from tachikoma.events import AgentEvent, Error, Result


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
    ) -> None:
        self._allowed_tools = allowed_tools or []
        self._model = model
        self._client: ClaudeSDKClient | None = None

    async def __aenter__(self) -> "Coordinator":
        options = ClaudeAgentOptions(
            allowed_tools=self._allowed_tools,
            model=self._model,
        )
        self._client = ClaudeSDKClient(options)
        await self._client.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None

    async def send_message(self, text: str) -> AsyncIterator[AgentEvent]:
        """Send a user message and yield AgentEvents as the agent responds."""
        if self._client is None:
            raise RuntimeError("Coordinator is not connected. Use as an async context manager.")

        await self._client.query(text)

        try:
            async for sdk_message in self._client.receive_messages():
                events = adapt(sdk_message)

                for event in events:
                    yield event

                if any(isinstance(e, Result) for e in events):
                    break

        except (CLIConnectionError, ProcessError) as exc:
            yield Error(message=str(exc), recoverable=True)

    async def interrupt(self) -> None:
        """Interrupt the current agent response."""
        if self._client is not None:
            await self._client.interrupt()
