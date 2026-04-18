"""Terminal REPL: interactive channel for the agent using prompt_toolkit."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from bubus import EventBus
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.validation import Validator
from rich.console import Console
from rich.markdown import Markdown

from tachikoma.buffer.events import BufferedDelivery
from tachikoma.channel import Channel
from tachikoma.display import TOOL_DISPLAY, format_tool_name
from tachikoma.events import AgentEvent, Error, Result, Status, TextChunk, ToolActivity

if TYPE_CHECKING:
    from tachikoma.coordinator import Coordinator

_log = logger.bind(component="repl")


class Renderer:
    """Renders AgentEvents to the terminal via rich Console."""

    def __init__(
        self,
        console: Console | None = None,
        err_console: Console | None = None,
    ) -> None:
        self._console = console or Console()
        self._err_console = err_console or Console(stderr=True)

    def render(self, event: AgentEvent) -> bool:
        """Render a single AgentEvent to the terminal.

        Returns True if the REPL should continue, False if it should exit.
        """
        if isinstance(event, Status):
            self._console.print(event.message, style="dim italic grey50", highlight=False)

        elif isinstance(event, TextChunk):
            self._console.print(Markdown(event.text, code_theme="dracula"))

        elif isinstance(event, ToolActivity):
            display_fn = TOOL_DISPLAY.get(event.tool_name)
            name = format_tool_name(event.tool_name)
            label = display_fn(event.tool_input) if display_fn else f"{name}..."
            self._console.print(f"🔧 {label}", style="dim italic grey50", highlight=False)

        elif isinstance(event, Result):
            self._console.print()

        elif isinstance(event, Error):
            self._err_console.print(f"Error: {event.message}", style="bold red")

            if not event.recoverable:
                return False

        return True


class Repl(Channel):
    """Terminal REPL that sends user input through the coordinator.

    When an event bus is provided, the REPL subscribes to:
    - BufferedDelivery: Deferred notifications and session tasks from the priority buffer
    """

    def __init__(
        self,
        history_path: Path,
        bus: EventBus | None = None,
    ) -> None:
        self.__coordinator: Coordinator | None = None
        self._renderer = Renderer()
        self._bus = bus
        self._task_queue: asyncio.Queue[BufferedDelivery] = asyncio.Queue()

        kb = KeyBindings()

        @kb.add("enter")
        def _submit(event: KeyPressEvent) -> None:
            event.current_buffer.validate_and_handle()

        @kb.add("escape", "enter")
        def _newline(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        self._session = PromptSession[str](
            multiline=True,
            history=FileHistory(str(history_path)),
            prompt_continuation="  ",
            key_bindings=kb,
            validator=Validator.from_callable(
                lambda text: text.strip() != "",
                error_message="",
                move_cursor_to_end=True,
            ),
        )

    @property
    def _coordinator(self) -> Coordinator:
        assert self.__coordinator is not None, "run() must be called before processing messages"
        return self.__coordinator

    async def run(self, coordinator: Coordinator) -> None:
        """Run the REPL input loop until the user exits.

        Between user inputs, the loop checks for queued session tasks
        from the event bus and processes them.
        """
        self.__coordinator = coordinator

        # Subscribe to buffered delivery events — deferred to run() so coordinator is set
        if self._bus is not None:
            self._bus.on(BufferedDelivery, self._handle_buffered_delivery)
        _log.debug("REPL started")

        while True:
            # Process any queued session tasks before prompting
            await self._process_queued_tasks()

            try:
                text = await self._session.prompt_async("> ")
            except (KeyboardInterrupt, EOFError):
                _log.debug("REPL interrupted by user")
                break

            if text.strip().lower() in ("exit", "quit"):
                _log.debug("REPL exited by command")
                break

            _log.debug("Message received: length={n}", n=len(text))

            try:
                self._coordinator.enqueue(text)
                async for event in self._coordinator.send_message():
                    if not self._renderer.render(event):
                        return
            except KeyboardInterrupt:
                await self._coordinator.interrupt()
                break

    async def _process_queued_tasks(self) -> None:
        """Process any queued buffered deliveries without blocking.

        Drains the queue of all pending items and processes them.
        """
        while not self._task_queue.empty():
            try:
                event = self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            await self._execute_buffered_delivery(event)

    async def _execute_through_coordinator(self, prompt: str) -> bool:
        """Send a prompt through the coordinator and render the response.

        Returns False if the REPL should exit.
        """
        try:
            self._coordinator.enqueue(prompt)
            async for ev in self._coordinator.send_message():
                if not self._renderer.render(ev):
                    return False
        except Exception as e:
            _log.exception("Error processing message through coordinator")
            self._renderer._err_console.print(
                f"Error: {e}",
                style="bold red",
            )
        return True

    async def _execute_buffered_delivery(self, event: BufferedDelivery) -> None:
        """Execute a buffered delivery by sending it through the coordinator."""
        _log.info(
            "Processing buffered delivery: items={count}, shutdown={is_shutdown}",
            count=len(event.items),
            is_shutdown=event.is_shutdown_digest,
        )

        label = "Shutdown digest" if event.is_shutdown_digest else "Scheduled task"
        self._renderer._console.print(
            f"\n[dim italic]📋 {label}:[/dim italic]",
        )

        if not await self._execute_through_coordinator(event.prompt):
            return

        for item in event.items:
            if item.on_delivered is not None:
                await item.on_delivered()

        if event.is_shutdown_digest:
            buffer = getattr(self, "_buffer", None)
            if buffer is not None:
                buffer.resolve_shutdown()

    async def _handle_buffered_delivery(self, event: BufferedDelivery) -> None:
        """Handle a BufferedDelivery event from the buffer.

        Queues the delivery for processing in the main REPL loop.
        """
        _log.debug("Queueing buffered delivery: items={count}", count=len(event.items))
        await self._task_queue.put(event)
