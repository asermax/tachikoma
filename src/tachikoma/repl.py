"""Terminal REPL: interactive channel for the agent using prompt_toolkit."""

import asyncio
from pathlib import Path

from bubus import EventBus
from loguru import logger
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.validation import Validator
from rich.console import Console
from rich.markdown import Markdown

from tachikoma.coordinator import Coordinator
from tachikoma.display import TOOL_DISPLAY
from tachikoma.events import AgentEvent, Error, Result, Status, TextChunk, ToolActivity
from tachikoma.tasks.events import SessionTaskReady, TaskNotification

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
            label = display_fn(event.tool_input) if display_fn else f"{event.tool_name}..."
            self._console.print(f"🔧 {label}", style="dim italic grey50", highlight=False)

        elif isinstance(event, Result):
            self._console.print()

        elif isinstance(event, Error):
            self._err_console.print(f"Error: {event.message}", style="bold red")

            if not event.recoverable:
                return False

        return True


class Repl:
    """Terminal REPL that sends user input through the coordinator.

    When an event bus is provided, the REPL subscribes to:
    - SessionTaskReady: Proactive tasks from the task scheduler
    - TaskNotification: Completion/failure notifications from background tasks
    """

    def __init__(
        self,
        coordinator: Coordinator,
        history_path: Path,
        bus: EventBus | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._renderer = Renderer()
        self._bus = bus
        self._task_queue: asyncio.Queue[SessionTaskReady] = asyncio.Queue()

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

        # Subscribe to task events if bus is provided
        if self._bus is not None:
            self._bus.on(SessionTaskReady, self._handle_session_task)
            self._bus.on(TaskNotification, self._handle_notification)

    async def run(self) -> None:
        """Run the REPL input loop until the user exits.

        Between user inputs, the loop checks for queued session tasks
        from the event bus and processes them.
        """
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
        """Process any queued session tasks without blocking.

        Drains the queue of all pending tasks and processes them.
        """
        while not self._task_queue.empty():
            try:
                event = self._task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            await self._execute_session_task(event)

    async def _execute_session_task(self, event: SessionTaskReady) -> None:
        """Execute a session task by sending it through the coordinator."""
        instance = event.instance
        _log.info(
            "Processing session task: id={task_id}, prompt_preview={preview}",
            task_id=instance.id,
            preview=instance.prompt[:50] if instance.prompt else "",
        )

        self._renderer._console.print(
            "\n[dim italic]📋 Scheduled task:[/dim italic]",
        )

        try:
            self._coordinator.enqueue(instance.prompt)
            async for ev in self._coordinator.send_message():
                if not self._renderer.render(ev):
                    return

            # Mark task as completed via callback
            if event.on_complete is not None:
                await event.on_complete()

        except Exception as e:
            _log.exception("Error during session task processing")
            self._renderer._err_console.print(
                f"Error processing task: {e}",
                style="bold red",
            )

    async def _handle_session_task(self, event: SessionTaskReady) -> None:
        """Handle a SessionTaskReady event from the task scheduler.

        Queues the task for processing in the main REPL loop.
        """
        _log.debug("Queueing session task: id={task_id}", task_id=event.instance.id)
        await self._task_queue.put(event)

    async def _handle_notification(self, event: TaskNotification) -> None:
        """Handle a TaskNotification event from the background task executor.

        Notifications are printed directly to the console.
        """
        severity_style = "dim italic blue" if event.severity == "info" else "bold yellow"
        severity_label = "ℹ️" if event.severity == "info" else "⚠️"

        _log.info(
            "Task notification: severity={severity}, source={source}",
            severity=event.severity,
            source=event.source_task_id,
        )

        self._renderer._console.print(
            f"\n{severity_label} [{severity_style}]{event.message}[/{severity_style}]",
        )
