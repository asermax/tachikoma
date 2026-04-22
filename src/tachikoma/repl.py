"""Terminal REPL: interactive channel for the agent using prompt_toolkit."""

from __future__ import annotations

import asyncio
import contextlib
import signal
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
from tachikoma.updates.events import RestartRequested

if TYPE_CHECKING:
    from tachikoma.buffer.buffer import Buffer
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
        buffer: Buffer | None = None,
    ) -> None:
        self.__coordinator: Coordinator | None = None
        self._renderer = Renderer()
        self._bus = bus
        self._buffer: Buffer | None = buffer
        self._restart_requested: bool = False

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested
        # Serialize delivery vs. prompt-driven coordinator usage
        self._delivery_lock = asyncio.Lock()
        self._delivery_tasks: set[asyncio.Task] = set()

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

    def attach_buffer(self, buffer: Buffer) -> None:
        self._buffer = buffer

    async def run(self, coordinator: Coordinator) -> None:
        """Run the REPL input loop until the user exits.

        On exit, drains any remaining buffered items as a single shutdown
        digest before returning, so the digest delivery happens while the
        coordinator and BufferedDelivery subscription are still alive.
        """
        self.__coordinator = coordinator

        # Subscribe to buffered delivery events — deferred to run() so coordinator is set
        if self._bus is not None:
            self._bus.on(BufferedDelivery, self._handle_buffered_delivery)
            self._bus.on(RestartRequested, self._handle_restart_requested)
        _log.debug("REPL started")

        try:
            while True:
                if self._restart_requested:
                    _log.debug("REPL exiting for restart")
                    break

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
                    async with self._delivery_lock:
                        self._coordinator.enqueue(text)
                        async for event in self._coordinator.send_message():
                            if not self._renderer.render(event):
                                return
                except KeyboardInterrupt:
                    await self._coordinator.interrupt()
                    break
        finally:
            force_exit = await self._flush_buffer_on_shutdown()
            if force_exit:
                # KD-6/S15: abort shutdown immediately; skip post-processing
                raise KeyboardInterrupt

    async def _handle_restart_requested(self, event: RestartRequested) -> None:
        """Handle restart request from the update subsystem."""
        self._restart_requested = True

    async def _flush_buffer_on_shutdown(self) -> bool:
        """Drain remaining buffered items as a shutdown digest.

        Installs a SIGINT handler for the duration of the flush so a second
        Ctrl+C cancels the flush AND any in-flight coordinator exchange
        (KD-6/S15). Returns True when the flush was force-cancelled by a
        second SIGINT — the caller should then exit immediately.
        """
        if self._buffer is None:
            return False

        loop = asyncio.get_running_loop()

        flush_task: asyncio.Task[None] = asyncio.create_task(self._buffer.flush_on_shutdown())
        interrupt_task: asyncio.Task[None] | None = None
        force_exit_triggered = False

        def _force_exit() -> None:
            nonlocal force_exit_triggered, interrupt_task
            force_exit_triggered = True
            _log.warning("Second SIGINT during shutdown flush — abandoning digest")
            flush_task.cancel()
            interrupt_task = asyncio.create_task(self._coordinator.interrupt())

        previous_handler: object = signal.getsignal(signal.SIGINT)
        try:
            loop.add_signal_handler(signal.SIGINT, _force_exit)
        except (NotImplementedError, RuntimeError):
            previous_handler = None

        try:
            await flush_task
        except asyncio.CancelledError:
            _log.info("Shutdown flush cancelled by second SIGINT")
        finally:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(signal.SIGINT)

            if callable(previous_handler):
                signal.signal(signal.SIGINT, previous_handler)

            if interrupt_task is not None:
                with contextlib.suppress(Exception):
                    await interrupt_task

        return force_exit_triggered

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

    async def _handle_buffered_delivery(self, event: BufferedDelivery) -> None:
        """Handle a BufferedDelivery event from the buffer.

        Spawns a detached task for the delivery work so the EventBus is freed
        immediately. The task acquires the delivery lock and processes through
        the coordinator pipeline.
        """
        _log.debug("Delivering buffered event inline: items={count}", count=len(event.items))

        task = asyncio.create_task(self._deliver(event))

        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _deliver(self, event: BufferedDelivery) -> None:
        """Execute a buffered delivery as a detached task."""
        try:
            async with self._delivery_lock:
                await self._execute_buffered_delivery(event)
        except Exception:
            _log.exception(
                "Error in detached delivery task: items={count}, shutdown={is_shutdown}",
                count=len(event.items),
                is_shutdown=event.is_shutdown_digest,
            )
        finally:
            if event.is_shutdown_digest and self._buffer is not None:
                self._buffer.resolve_shutdown()
