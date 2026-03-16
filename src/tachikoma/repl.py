"""Terminal REPL: interactive channel for the agent using prompt_toolkit."""

from pathlib import Path

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
            self._console.print(label, style="dim italic grey50", highlight=False)

        elif isinstance(event, Result):
            self._console.print()

        elif isinstance(event, Error):
            self._err_console.print(f"Error: {event.message}", style="bold red")

            if not event.recoverable:
                return False

        return True


class Repl:
    """Terminal REPL that sends user input through the coordinator."""

    def __init__(self, coordinator: Coordinator, history_path: Path) -> None:
        self._coordinator = coordinator
        self._renderer = Renderer()

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

    async def run(self) -> None:
        """Run the REPL input loop until the user exits."""
        _log.debug("REPL started")

        while True:
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
                async for event in self._coordinator.send_message(text):
                    if not self._renderer.render(event):
                        return
            except KeyboardInterrupt:
                await self._coordinator.interrupt()
                break
