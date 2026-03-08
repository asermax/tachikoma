"""Terminal REPL: interactive channel for the agent using prompt_toolkit."""

import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.validation import Validator

from tachikoma.coordinator import Coordinator
from tachikoma.events import AgentEvent, Error, Result, TextChunk, ToolActivity

HISTORY_PATH = Path.home() / ".tachikoma" / "repl_history"

TOOL_DISPLAY = {
    "Read": lambda inp: f"Reading {inp.get('file_path', '...')}...",
    "Grep": lambda inp: f"Searching for '{inp.get('pattern', '...')}'...",
    "Glob": lambda inp: f"Globbing {inp.get('pattern', '...')}...",
    "Bash": lambda inp: f"Running: {inp.get('command', '...')}",
    "ToolSearch": lambda inp: f"Searching tools: {inp.get('query', '...')}",
}

# Gray italic (ANSI: 90 = bright black/gray, 3 = italic)
TOOL_STYLE = "\033[3;90m"
RESET = "\033[0m"


class Renderer:
    """Renders AgentEvents to the terminal, tracking line state."""

    def __init__(self) -> None:
        self._needs_newline = False

    def render(self, event: AgentEvent) -> bool:
        """Render a single AgentEvent to the terminal.

        Returns True if the REPL should continue, False if it should exit.
        """
        if isinstance(event, TextChunk):
            print(event.text, end="", flush=True)
            self._needs_newline = True

        elif isinstance(event, ToolActivity):
            if self._needs_newline:
                print()
                self._needs_newline = False

            display_fn = TOOL_DISPLAY.get(event.tool_name)
            label = display_fn(event.tool_input) if display_fn else f"{event.tool_name}..."
            print(f"{TOOL_STYLE}{label}{RESET}")

        elif isinstance(event, Result):
            print()
            self._needs_newline = False

        elif isinstance(event, Error):
            print(f"\nError: {event.message}", file=sys.stderr)
            self._needs_newline = False

            if not event.recoverable:
                return False

        return True


class Repl:
    """Terminal REPL that sends user input through the coordinator."""

    def __init__(self, coordinator: Coordinator) -> None:
        self._coordinator = coordinator
        self._renderer = Renderer()

        self._session = PromptSession[str](
            history=FileHistory(HISTORY_PATH),
            validator=Validator.from_callable(
                lambda text: text.strip() != "",
                error_message="",
                move_cursor_to_end=True,
            ),
        )

    async def run(self) -> None:
        """Run the REPL input loop until the user exits."""
        while True:
            try:
                text = await self._session.prompt_async("you> ")
            except (KeyboardInterrupt, EOFError):
                break

            if text.strip().lower() in ("exit", "quit"):
                break

            try:
                async for event in self._coordinator.send_message(text):
                    if not self._renderer.render(event):
                        return
            except KeyboardInterrupt:
                await self._coordinator.interrupt()
                break
