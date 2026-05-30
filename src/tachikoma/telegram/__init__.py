"""Telegram channel: bot communication and response rendering.

This module provides the Telegram bot integration for Tachikoma,
including progressive message editing, tool activity display,
and streaming response rendering.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import sys
import termios
import time
import tty
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from os.path import basename
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.dispatcher import BackoffConfig
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    Message,
    MessageReactionUpdated,
    ReactionType,
    ReactionTypeEmoji,
)
from aiogram.utils.chat_action import ChatActionSender
from bubus import EventBus
from claude_agent_sdk.types import McpSdkServerConfig
from loguru import logger
from telegramify_markdown import convert, split_entities, utf16_len

from tachikoma.adapter import sanitize_text
from tachikoma.bootstrap import BootstrapContext, BootstrapError
from tachikoma.buffer.events import BufferedDelivery
from tachikoma.channel import Channel
from tachikoma.config import TelegramSettings
from tachikoma.display import _format_bash_summary, format_tool_name, summarize_tool_activity
from tachikoma.events import Error, Result, Status, TextChunk, ToolActivity
from tachikoma.media import (
    MEDIA_TEMP_DIR,
    MediaTooLargeError,
    build_description,
    download_media,
    generate_media_filename,
    resolve_media,
)
from tachikoma.message import ButtonTapMessage, ReactionMessage, TextMessage
from tachikoma.telegram.buttons import _unpack, create_buttons_server
from tachikoma.telegram.pinning import create_pinning_server
from tachikoma.telegram.tools import create_send_file_server
from tachikoma.updates.events import RestartRequested

if TYPE_CHECKING:
    from tachikoma.buffer.buffer import Buffer
    from tachikoma.coordinator import Coordinator


_log = logger.bind(component="telegram")

# Telegram hard limit in UTF-16 code units
TELEGRAM_MAX_UTF16 = 4096

# Time-based throttle interval for edits (seconds)
EDIT_THROTTLE_INTERVAL = 2.0

# Reply-to context truncation limits (characters)
_TRUNCATE_HEAD = 100
_TRUNCATE_TAIL = 100


def _truncate_reply_text(text: str) -> str:
    """Truncate long reply-to text, keeping head and tail with ellipsis."""
    if len(text) <= _TRUNCATE_HEAD + _TRUNCATE_TAIL:
        return text
    return text[:_TRUNCATE_HEAD] + "[...]" + text[-_TRUNCATE_TAIL:]


def code_wrap(text: str) -> str:
    """Wrap text in markdown inline code span, handling backtick collision.

    Single backticks for normal text; double backticks with space padding
    when content contains backticks (per CommonMark 6.1).
    """
    if "`" not in text:
        return f"`{text}`"

    return f"`` {text} ``"


def _emoji_set(reactions: Sequence[ReactionType]) -> frozenset[str]:
    """Extract emoji strings from ReactionTypeEmoji entries, ignoring custom/paid."""
    return frozenset(r.emoji for r in reactions if isinstance(r, ReactionTypeEmoji))


# Telegram-specific live tool line formatters (present-progressive, full paths)
# Included tools apply code_wrap() to arguments; excluded tools produce plain text
TELEGRAM_TOOL_DISPLAY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: f"Reading {code_wrap(inp.get('file_path', '...'))}",
    "Grep": lambda inp: f"Searching for {code_wrap(inp.get('pattern', '...'))}",
    "Glob": lambda inp: f"Globbing {code_wrap(inp.get('pattern', '...'))}",
    "Bash": lambda inp: (
        inp["description"]
        if inp.get("description")
        else f"Running: {code_wrap(inp.get('command', '...'))}"
    ),
    "Edit": lambda inp: f"Editing {code_wrap(inp.get('file_path', '...'))}",
    "Write": lambda inp: f"Writing {code_wrap(inp.get('file_path', '...'))}",
    "Agent": lambda inp: f"Agent: {inp['description']}" if "description" in inp else "Agent...",
    "ToolSearch": lambda inp: f"Searching tools: {inp.get('query', '...')}",
}


# Telegram-specific summary formatters (present-progressive, basenames)
# Inline code provides visual grouping, so single quotes are omitted on Grep/Glob
TELEGRAM_TOOL_SUMMARY: dict[str, Callable[[dict[str, Any]], str]] = {
    "Read": lambda inp: (
        f"reading {code_wrap(basename(inp['file_path']))}"
        if "file_path" in inp
        else "reading a file"
    ),
    "Grep": lambda inp: (
        f"searching for {code_wrap(inp['pattern'])}"
        if "pattern" in inp
        else "searching for a pattern"
    ),
    "Glob": lambda inp: (
        f"globbing {code_wrap(inp['pattern'])}" if "pattern" in inp else "globbing a pattern"
    ),
    "Bash": lambda inp: _format_bash_summary(inp),
    "Edit": lambda inp: (
        f"editing {code_wrap(basename(inp['file_path']))}"
        if "file_path" in inp
        else "editing a file"
    ),
    "Write": lambda inp: (
        f"writing {code_wrap(basename(inp['file_path']))}"
        if "file_path" in inp
        else "writing a file"
    ),
    "Agent": lambda inp: (
        f"agent: {inp['description']}" if "description" in inp else "dispatched an agent"
    ),
    "ToolSearch": lambda _: "searching tools",
}


class ResponseRenderer:
    """Renders agent events as Telegram messages with progressive editing.

    The renderer accumulates text in a buffer and periodically edits a single
    Telegram message to show the streaming response. Tool activity appears as
    an inline status line within the message.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        push_notifications: bool = False,
        *,
        is_pinned: Callable[[int], bool] | None = None,
    ) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._push_notifications = push_notifications
        self._is_pinned = is_pinned
        self._current_message_id: int | None = None
        self._buffer: str = ""
        self._tool_line: str | None = None
        self._tool_activities: list[ToolActivity] = []
        self._last_edit_time: float = 0.0
        self._message_count: int = 0
        self._split_message_ids: list[int] = []

    def reset(self) -> None:
        """Clear all state for a new response.

        Called after each Result event to prepare for the next turn.
        """
        self._current_message_id = None
        self._buffer = ""
        self._tool_line = None
        self._tool_activities = []
        self._last_edit_time = 0.0
        self._split_message_ids = []
        # Note: _message_count is NOT reset - it tracks total messages
        # for the entire response cycle including buffered messages

    def has_sent_content(self) -> bool:
        """Whether any message has been sent in the current response."""
        return self._current_message_id is not None

    def get_last_message_id(self) -> int | None:
        """Return the Telegram message ID of the last sent response, or None."""
        return self._current_message_id

    def get_finalized_message_ids(self) -> list[int]:
        """Return all message IDs from the finalized response.

        Returns the main message ID plus any split message IDs.
        Returns an empty list if no content was sent.
        """
        if self._current_message_id is None:
            return []

        ids: list[int] = []
        if self._split_message_ids:
            ids.extend(self._split_message_ids)
        else:
            ids.append(self._current_message_id)
        return ids

    async def handle_status(self, message: str) -> None:
        """Handle a Status event by sending a transient status message.

        The message will be replaced when the first TextChunk or ToolActivity arrives.
        If a message is already showing, its content is updated in place.
        """
        if self._current_message_id is not None:
            try:
                await self._bot.edit_message_text(
                    f"_{message}_",
                    chat_id=self._chat_id,
                    message_id=self._current_message_id,
                    parse_mode="Markdown",
                )
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    _log.debug("Status edit skipped: message content unchanged")
                else:
                    _log.exception("Failed to edit status message")
            except TelegramAPIError:
                _log.exception("Failed to edit status message")
            return

        try:
            msg = await self._bot.send_message(
                self._chat_id,
                f"_{message}_",
                parse_mode="Markdown",
                disable_notification=self._push_notifications,
            )
            self._current_message_id = msg.message_id
            self._message_count += 1
        except TelegramAPIError:
            _log.exception("Failed to send status message")

    async def handle_text(self, chunk: str) -> None:
        """Handle a TextChunk event by appending to buffer and scheduling edit."""
        # If we had tools and this is the first text after them,
        # insert the summary marker
        if self._tool_activities:
            if self._buffer and not self._buffer.endswith("\n"):
                self._buffer += "\n"

            prefix = "\n" if self._buffer else ""
            summary = summarize_tool_activity(
                self._tool_activities,
                summary_map=TELEGRAM_TOOL_SUMMARY,
            )
            self._buffer += f"{prefix}*🔧 {summary}*\n\n"
            self._tool_activities = []  # Clear — each transition gets independent summary
            self._tool_line = None  # Clear tool line - marker replaces it

        self._buffer += chunk
        await self._flush(force=False)

    async def handle_tool(self, activity: ToolActivity) -> None:
        """Handle a ToolActivity event by setting the tool line."""
        # Append activity for summary generation at tool→text transition
        self._tool_activities.append(activity)

        # Update live tool line display
        display_fn = TELEGRAM_TOOL_DISPLAY.get(activity.tool_name)
        name = format_tool_name(activity.tool_name)
        label = display_fn(activity.tool_input) if display_fn else f"{name}..."
        self._tool_line = f"*🔧 {label}*"
        await self._flush(force=False)

    async def handle_error(self, error: Error) -> None:
        """Handle an Error event by sending a separate error message."""
        error_text = sanitize_text(f"⚠️ Error: {error.message}")

        # Send silently if push notifications are enabled AND content was already streamed
        # (the copy+delete will provide the push notification)
        silent = self._push_notifications and self._current_message_id is not None

        try:
            await self._bot.send_message(
                self._chat_id,
                error_text,
                parse_mode=None,
                disable_notification=silent,
            )
        except TelegramAPIError:
            _log.exception("Failed to send error message")

        if not error.recoverable:
            _log.error("Non-recoverable error: {msg}", msg=error.message)

    async def finalize(self) -> None:
        """Send the final state of the current message, bypassing throttle."""
        if self._tool_activities:
            if self._buffer and not self._buffer.endswith("\n"):
                self._buffer += "\n"

            prefix = "\n" if self._buffer else ""
            summary = summarize_tool_activity(
                self._tool_activities,
                summary_map=TELEGRAM_TOOL_SUMMARY,
            )
            self._buffer += f"{prefix}*🔧 {summary}*\n"
            self._tool_activities = []

        self._tool_line = None
        await self._flush(force=True)

    async def notify(self) -> None:
        """Copy+delete the last message to trigger a push notification.

        No-op when push notifications are disabled or no message was sent.
        Safe ordering: copy first, skip delete on failure.
        """
        if not self._push_notifications or self._current_message_id is None:
            return

        # Pinned messages are left untouched — the pin action itself
        # delivered the push notification via disable_notification=False.
        if self._is_pinned is not None and self._is_pinned(self._current_message_id):
            _log.debug(
                "Skipping push notification copy+delete for pinned message: id={id}",
                id=self._current_message_id,
            )
            return

        # Try copy_message — on failure, preserve original
        try:
            await self._bot.copy_message(
                chat_id=self._chat_id,
                from_chat_id=self._chat_id,
                message_id=self._current_message_id,
            )
        except TelegramAPIError:
            _log.warning(
                "Failed to copy message for push notification: message_id={id}",
                id=self._current_message_id,
            )
            return  # Skip delete — original is preserved

        # Try delete_message with retries — on final failure, accept duplicate
        for attempt in range(3):
            try:
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=self._current_message_id,
                )
                return  # Success
            except TelegramAPIError:
                if attempt < 2:
                    await asyncio.sleep(0.5)
                else:
                    _log.warning(
                        "Failed to delete original after copy (duplicate visible): message_id={id}",
                        id=self._current_message_id,
                    )

    async def _flush(self, force: bool = False) -> None:
        """Send/edit the message with current buffer and tool line.

        Args:
            force: If True, bypass throttle timer (used for finalization).
        """
        # Check throttle (skip if forced)
        now = time.monotonic()
        if not force and (now - self._last_edit_time) < EDIT_THROTTLE_INTERVAL:
            return

        # Compose display text
        display_text = self._buffer
        if self._tool_line:
            if display_text:
                display_text += f"\n\n{self._tool_line}"
            else:
                display_text = self._tool_line

        # Handle empty state (nothing to send yet)
        if not display_text:
            return

        # Sanitize before API call — strips surrogates from tool labels/input
        display_text = sanitize_text(display_text)

        # Convert markdown to Telegram entities format
        text, entities = convert(display_text)

        # Split if converted text exceeds Telegram's UTF-16 limit
        if utf16_len(text) > TELEGRAM_MAX_UTF16:
            chunks = split_entities(text, entities, TELEGRAM_MAX_UTF16)
            await self._send_chunks(chunks)
            self._last_edit_time = time.monotonic()
            return

        # Handle shrink-to-unsplit: content was split but now fits
        if self._split_message_ids:
            first_id = self._split_message_ids[0]
            excess_ids = self._split_message_ids[1:]
            self._split_message_ids = []
            self._current_message_id = first_id

            # Delete excess tracked messages
            for old_id in excess_ids:
                try:
                    await self._bot.delete_message(
                        chat_id=self._chat_id,
                        message_id=old_id,
                    )
                except TelegramAPIError:
                    _log.warning(
                        "Failed to delete excess split message: id={id}",
                        id=old_id,
                    )
            # Fall through to edit first_id with full content

        try:
            if self._current_message_id is None:
                # Send new message
                msg = await self._bot.send_message(
                    self._chat_id,
                    text,
                    parse_mode=None,
                    entities=[e.to_dict() for e in entities],  # type: ignore[arg-type]
                    disable_notification=self._push_notifications,
                )
                self._current_message_id = msg.message_id
                self._message_count += 1
                _log.debug(
                    "Sent message: id={id}, count={n}",
                    id=self._current_message_id,
                    n=self._message_count,
                )
            else:
                # Edit existing message
                await self._bot.edit_message_text(
                    text=text,
                    chat_id=self._chat_id,
                    message_id=self._current_message_id,
                    parse_mode=None,
                    entities=[e.to_dict() for e in entities],  # type: ignore[arg-type]
                )
                _log.debug("Edited message: id={id}", id=self._current_message_id)

            self._last_edit_time = now

        except TelegramRetryAfter as e:
            # Rate limited - wait and continue
            _log.warning(
                "Rate limited, waiting {s}s",
                s=e.retry_after,
            )

            await asyncio.sleep(e.retry_after)
            # Don't retry this edit - next edit cycle will pick up the buffer

        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                _log.debug("Edit skipped: message content unchanged")
            else:
                _log.exception("Failed to send/edit message")

        except TelegramAPIError:
            _log.exception("Failed to send/edit message")

    async def _send_chunks(
        self,
        chunks: list[tuple[str, list]],
    ) -> None:
        """Send multiple pre-split chunks as separate Telegram messages.

        Tracks split message IDs so re-splits reuse existing messages via
        edit-in-place instead of creating duplicates. Excess messages from
        a previous split with more chunks are deleted.
        """
        old_ids = self._split_message_ids
        self._split_message_ids = []

        for i, (text, entities) in enumerate(chunks):
            if i < len(old_ids) and old_ids[i] != -1:
                # Reuse existing split message — edit in-place
                try:
                    await self._bot.edit_message_text(
                        text=text,
                        chat_id=self._chat_id,
                        message_id=old_ids[i],
                        parse_mode=None,
                        entities=[e.to_dict() for e in entities],
                    )
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e):
                        _log.exception("Failed to edit split message")
                except TelegramAPIError:
                    _log.exception("Failed to edit split message")

                self._split_message_ids.append(old_ids[i])

            elif i == 0 and self._current_message_id is not None:
                # First split ever: edit the streaming message
                try:
                    await self._bot.edit_message_text(
                        text=text,
                        chat_id=self._chat_id,
                        message_id=self._current_message_id,
                        parse_mode=None,
                        entities=[e.to_dict() for e in entities],
                    )
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e):
                        _log.exception("Failed to edit message")
                except TelegramAPIError:
                    _log.exception("Failed to edit message")

                self._split_message_ids.append(self._current_message_id)

            else:
                # Need a new message
                try:
                    msg = await self._bot.send_message(
                        self._chat_id,
                        text,
                        parse_mode=None,
                        entities=[e.to_dict() for e in entities],
                        disable_notification=self._push_notifications,
                    )
                    self._split_message_ids.append(msg.message_id)
                    self._message_count += 1
                    _log.debug(
                        "Sent message: id={id}, count={n}",
                        id=msg.message_id,
                        n=self._message_count,
                    )
                except TelegramAPIError:
                    _log.exception("Failed to send split message")
                    # Append sentinel so indices stay aligned for re-split
                    self._split_message_ids.append(-1)

        # Update current message to last chunk
        if self._split_message_ids:
            self._current_message_id = self._split_message_ids[-1]

        # Delete excess old messages (skip sentinels from failed sends)
        for old_id in old_ids[len(chunks) :]:
            if old_id == -1:
                continue
            try:
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=old_id,
                )
            except TelegramAPIError:
                _log.warning(
                    "Failed to delete excess split message: id={id}",
                    id=old_id,
                )


class TelegramChannel(Channel):
    """Telegram bot channel that receives messages and renders agent responses.

    The channel uses aiogram for long polling and message handling.
    Mid-stream messages are buffered via the coordinator's ``enqueue()``
    and processed after the current response completes.  Supports graceful
    shutdown with partial response delivery.

    When an event bus is provided, the channel subscribes to:
    - BufferedDelivery: Deferred notifications and session tasks from the priority buffer
    """

    def __init__(
        self,
        settings: TelegramSettings,
        workspace_path: Path,
        bus: EventBus | None = None,
        buffer: Buffer | None = None,
    ) -> None:
        self.__coordinator: Coordinator | None = None
        self._settings = settings
        self._workspace_path = workspace_path
        self._bot = Bot(token=settings.bot_token)
        self._dispatcher = Dispatcher()
        self._router = Router()
        self._active_renderer: ResponseRenderer | None = None
        self._delivery_lock: asyncio.Lock = asyncio.Lock()
        self._delivery_tasks: set[asyncio.Task] = set()
        self._bus = bus
        self._buffer: Buffer | None = buffer
        self._restart_requested: bool = False
        self._is_pinned: Callable[[int], bool] | None = None
        self._shutdown_message_id: int | None = None

        # Set up router with authorization filter
        self._router.message.filter(F.chat.id == settings.authorized_chat_id)

        # Register message handler
        self._router.message(F.text)(self._handle_message)

        # Register media handler (catch-all for all supported media types)
        self._router.message(
            F.photo
            | F.voice
            | F.audio
            | F.document
            | F.sticker
            | F.video
            | F.video_note
            | F.animation,
        )(self._handle_media)

        # Register callback query handler for inline button taps
        self._router.callback_query(F.data.startswith("btn"))(self._handle_callback_query)

        # Register message_reaction handler for emoji reactions (gated by config)
        if self._settings.inbound_reactions:
            self._router.message_reaction(
                F.chat.id == self._settings.authorized_chat_id,
                F.user.is_not(None),
            )(self._handle_reaction)

        # Include router in dispatcher
        self._dispatcher.include_router(self._router)

        # Register shutdown hook
        self._dispatcher.shutdown.register(self._on_shutdown)

    @property
    def restart_requested(self) -> bool:
        return self._restart_requested

    @property
    def _coordinator(self) -> Coordinator:
        assert self.__coordinator is not None, "run() must be called before processing messages"
        return self.__coordinator

    def attach_buffer(self, buffer: Buffer) -> None:
        self._buffer = buffer

    def get_mcp_servers(self) -> dict[str, McpSdkServerConfig]:
        server = create_send_file_server(
            self._bot,
            self._settings.authorized_chat_id,
            self._workspace_path,
            self._settings.send_file.extra_roots,
        )

        def get_msg_id() -> int | None:
            if self._active_renderer is not None:
                return self._active_renderer.get_last_message_id()
            return None

        pinning_server, is_pinned = create_pinning_server(
            self._bot,
            self._settings.authorized_chat_id,
            get_msg_id,
        )
        self._is_pinned = is_pinned

        buttons_server = create_buttons_server(
            self._bot,
            self._settings.authorized_chat_id,
        )
        return {
            "send-file": server,
            "telegram-pinning": pinning_server,
            "telegram-buttons": buttons_server,
        }

    def get_skill_sources(self) -> list[Path]:
        return [Path(__file__).parent / "skill"]

    async def run(self, coordinator: Coordinator) -> None:
        """Start the bot and begin polling for messages.

        This method blocks until the bot is stopped (via signal, 'q' keypress,
        or error). Signals are handled manually (not by aiogram) so that
        polling stops gracefully without cancelling the task — this allows the
        Coordinator's post-processing pipeline to run on shutdown.
        """
        self.__coordinator = coordinator
        coordinator.shutdown_status_callback = self._send_shutdown_status

        # Subscribe to buffered delivery events — deferred to run() so coordinator is set
        if self._bus is not None:
            self._bus.on(BufferedDelivery, self._handle_buffered_delivery)

            async def _on_restart_requested(event: RestartRequested) -> None:
                self._restart_requested = True
                await self._dispatcher.stop_polling()

            self._bus.on(RestartRequested, _on_restart_requested)
        _log.info(
            "Starting Telegram bot for chat {chat_id}",
            chat_id=self._settings.authorized_chat_id,
        )

        try:
            await self._bot.set_my_commands(
                [
                    BotCommand(command="new", description="Start a new conversation"),
                    BotCommand(command="queue", description="Defer message for later processing"),
                ]
            )
        except TelegramAPIError:
            _log.warning("Failed to register bot commands (non-critical)")

        loop = asyncio.get_running_loop()

        def _request_shutdown(sig: signal.Signals) -> None:
            _log.info("Received {sig}, stopping polling", sig=sig.name)
            asyncio.ensure_future(self._dispatcher.stop_polling())

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _request_shutdown, sig)

        # Watch stdin for 'q' keypress to allow graceful shutdown from the terminal.
        # Uses cbreak mode for character-at-a-time input while preserving normal output.
        stdin_fd: int | None = None
        old_termios: list | None = None

        if sys.stdin.isatty():
            stdin_fd = sys.stdin.fileno()
            old_termios = termios.tcgetattr(stdin_fd)
            tty.setcbreak(stdin_fd)

            def _on_stdin_readable() -> None:
                ch = sys.stdin.read(1)

                if not ch:
                    # EOF — stdin closed; remove reader to avoid busy-loop spin
                    loop.remove_reader(stdin_fd)
                    return

                if ch.lower() == "q":
                    _log.info("Received 'q' keypress, stopping polling")
                    asyncio.ensure_future(self._dispatcher.stop_polling())

            loop.add_reader(stdin_fd, _on_stdin_readable)

            _log.info(
                "Telegram bot running — send a message to start chatting "
                "(press 'q' or Ctrl+C to stop)"
            )
        else:
            _log.info("Telegram bot running — send a message to start chatting (Ctrl+C to stop)")

        try:
            await self._dispatcher.start_polling(
                self._bot,
                handle_signals=False,
                close_bot_session=False,
                allowed_updates=self._dispatcher.resolve_used_update_types(),
                backoff_config=BackoffConfig(
                    min_delay=1,
                    max_delay=60,
                    factor=2,
                    jitter=0.1,
                ),
            )
        finally:
            if stdin_fd is not None:
                loop.remove_reader(stdin_fd)

            if old_termios is not None and stdin_fd is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_termios)

            # Drain buffer as a single shutdown digest while coordinator and
            # subscription are still alive. A second signal during the flush
            # cancels both the digest AND any in-flight coordinator exchange
            # (KD-6/S15).
            force_exit = await self._flush_buffer_on_shutdown(loop)

            # Drain any remaining deferred messages before shutdown.
            await self._drain_deferred_queue()

            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)

            if force_exit:
                raise KeyboardInterrupt

    async def _flush_buffer_on_shutdown(self, loop: asyncio.AbstractEventLoop) -> bool:
        """Drain the buffer as a single shutdown digest, returning True if a
        second signal force-exited the flush (KD-6/S15)."""
        if self._buffer is None:
            return False

        flush_task: asyncio.Task[None] = asyncio.create_task(self._buffer.flush_on_shutdown())
        interrupt_task: asyncio.Task[None] | None = None
        force_exit_triggered = False

        def _force_exit_during_flush(sig: signal.Signals) -> None:
            nonlocal force_exit_triggered, interrupt_task
            force_exit_triggered = True
            _log.warning("Second {sig} during shutdown flush — abandoning digest", sig=sig.name)
            flush_task.cancel()
            interrupt_task = asyncio.create_task(self._coordinator.interrupt())

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _force_exit_during_flush, sig)

        try:
            await flush_task
        except asyncio.CancelledError:
            _log.info("Shutdown flush cancelled by second signal")

        if interrupt_task is not None:
            with contextlib.suppress(Exception):
                await interrupt_task

        return force_exit_triggered

    def _detect_command(self, message: Message) -> tuple[str | None, str]:
        """Detect a recognized bot command in the message.

        Inspects ``message.entities`` for the first ``bot_command`` entity at
        offset 0.  Returns ``(command_name, args)`` for recognized commands
        (``/new``, ``/queue``) with non-empty arguments.  All other cases —
        no entity, unrecognized command, bare command, whitespace-only args —
        return ``(None, full_text)`` so the message is treated normally.

        First command wins: ``/new /queue msg`` → ``("new", "/queue msg")``.
        """
        text = message.text or ""
        if not message.entities:
            return None, text

        entity = message.entities[0]
        if entity.type == "bot_command" and entity.offset == 0:
            raw = text[entity.offset : entity.offset + entity.length]
            name = raw.lstrip("/")
            if "@" in name:
                name = name.split("@", 1)[0]
            args = text[entity.offset + entity.length :].strip()
            if name in ("new", "queue") and args:
                return name, args
            return None, text

        return None, text

    def _extract_reply_target(self, message: Message) -> str | None:
        """Extract the replied-to message ID if present.

        Returns str(message.reply_to_message.message_id) when the
        message is a reply, None otherwise.
        """
        if message.reply_to_message is not None:
            return str(message.reply_to_message.message_id)
        return None

    def _build_reply_context(self, message: Message) -> str | None:
        """Build a message prefix from the replied-to message text.

        Returns a formatted prefix string from ``reply_to_message.text``
        or ``reply_to_message.caption``, or None when no text is available
        (stickers, voice, media without caption, non-reply messages).
        """
        if message.reply_to_message is None:
            return None
        text = message.reply_to_message.text or message.reply_to_message.caption
        if not text:
            return None
        stripped = text.strip()
        if not stripped:
            return None
        truncated = _truncate_reply_text(stripped)
        return f"Replied to:\n> {truncated}"

    async def _resolve_reply_target(self, reply_to_id: str) -> str | None:
        """Look up whether a replied-to message maps to a different session.

        Returns target_session_id if the reply maps to a different session,
        None otherwise. Used by message, media, and reaction handlers.
        """
        registry = self._coordinator._registry
        if registry is None:
            return None
        try:
            target_id = await registry.find_session_by_external_id("telegram", reply_to_id)
            if target_id is None:
                _log.debug(
                    "Reply-to target not found in DB: external_id={eid}",
                    eid=reply_to_id,
                )
                return None
            active = await registry.get_active_session()
            if active is not None and target_id != active.id:
                return target_id
            _log.debug(
                "Reply-to targets current session (no-op): external_id={eid} session_id={sid}",
                eid=reply_to_id,
                sid=target_id,
            )
        except Exception:
            _log.exception("Session lookup for reply-to failed (best-effort)")
        return None

    async def _handle_message(self, message: Message) -> None:
        """Handle an incoming message from the authorized user."""
        if not message.text or not message.text.strip():
            _log.debug("Ignoring empty or non-text message")
            return

        cmd_name, cmd_args = self._detect_command(message)

        reply_target = self._extract_reply_target(message)
        target_session_id: str | None = None
        if reply_target is not None:
            target_session_id = await self._resolve_reply_target(reply_target)

        message_prefix = self._build_reply_context(message)

        if cmd_name is not None:
            incoming = TextMessage(
                text=cmd_args,
                force_new=False if target_session_id else (cmd_name == "new"),
                external_id=str(message.message_id),
                target_session_id=target_session_id,
                message_prefix=message_prefix,
            )
        else:
            incoming = TextMessage(
                text=message.text.strip(),
                external_id=str(message.message_id),
                target_session_id=target_session_id,
                message_prefix=message_prefix,
            )

        if self._delivery_lock.locked():
            if target_session_id is not None:
                _log.debug(
                    "Deferring reply-to targeting different session: target={target}",
                    target=target_session_id,
                )
                self._coordinator.enqueue_deferred(incoming)
            elif cmd_name is not None:
                _log.debug("Deferring command: /{cmd}", cmd=cmd_name)
                self._coordinator.enqueue_deferred(incoming)
            else:
                _log.debug("Mid-stream steering message")
                self._coordinator.enqueue(incoming)
            return

        async with self._delivery_lock:
            if target_session_id is not None:
                _log.debug(
                    "Deferring reply-to for session switch: target={target}",
                    target=target_session_id,
                )
                self._coordinator.enqueue_deferred(incoming)
            else:
                self._coordinator.enqueue(incoming)
            await self._process_through_coordinator()
            await self._record_incoming_external_id(incoming.external_id)
            await self._drain_deferred_queue()

    async def _record_incoming_external_id(self, external_id: str | None) -> None:
        """Record an incoming message's external ID to the active session.

        Best-effort: errors are logged, not raised. Skips when external_id
        is None or the session registry is unavailable.
        """
        if external_id is None:
            return

        registry = self._coordinator._registry
        if registry is None:
            return

        try:
            active = await registry.get_active_session()
            if active is not None:
                await registry.record_channel_message(
                    active.id, "telegram", "incoming", external_id
                )
        except Exception:
            _log.warning(
                "Failed to record incoming external ID (best-effort): eid={eid}",
                eid=external_id,
                exc_info=True,
            )

    async def _drain_deferred_queue(self) -> None:
        """Process all deferred messages sequentially.

        Promotes each deferred message into the message buffer and delivers it
        through the coordinator pipeline. Errors are caught per-item so one
        failure does not block the rest of the queue.
        """
        while self._coordinator.has_deferred:
            self._coordinator.promote_next_deferred()
            try:
                await self._process_through_coordinator()
            except Exception:
                _log.exception("Error processing deferred message, continuing drain")

    async def _handle_media(self, message: Message) -> None:
        """Handle an incoming media message from the authorized user."""
        result = resolve_media(message)
        if result is None:
            _log.debug("Ignoring unresolvable media message")
            return

        media_obj, descriptor = result
        filename = generate_media_filename(descriptor, media_obj)
        dest_path = MEDIA_TEMP_DIR / filename

        # Download with error handling
        try:
            await download_media(self._bot, media_obj, dest_path)
        except MediaTooLargeError as e:
            _log.warning("Media file too large: size={size}", size=e.file_size)
            await self._bot.send_message(
                self._settings.authorized_chat_id,
                str(e),
            )
            return
        except TelegramAPIError:
            _log.exception("Failed to download media file")
            await self._bot.send_message(
                self._settings.authorized_chat_id,
                "Failed to download the file. Please try again.",
            )
            return

        # Build description and enqueue
        metadata_lines = descriptor.build_metadata(media_obj)
        description = build_description(
            descriptor.label,
            metadata_lines,
            dest_path,
            message.caption,
        )

        reply_target = self._extract_reply_target(message)
        target_session_id: str | None = None
        if reply_target is not None:
            target_session_id = await self._resolve_reply_target(reply_target)

        message_prefix = self._build_reply_context(message)

        envelope = TextMessage(
            text=description,
            external_id=str(message.message_id),
            target_session_id=target_session_id,
            message_prefix=message_prefix,
        )

        # Mid-exchange (any phase): enqueue-only so the coordinator's forwarder
        # routes this onto the live sdk_inbox as a steering message rather than
        # blocking on the lock and becoming a new turn. See _handle_message for
        # the full rationale.
        if self._delivery_lock.locked():
            if target_session_id is not None:
                _log.debug(
                    "Deferring media reply-to targeting different session: target={target}",
                    target=target_session_id,
                )
                self._coordinator.enqueue_deferred(envelope)
            else:
                _log.debug("Mid-stream steering media message")
                self._coordinator.enqueue(envelope)
            return

        async with self._delivery_lock:
            if target_session_id is not None:
                _log.debug(
                    "Deferring media reply-to for session switch: target={target}",
                    target=target_session_id,
                )
                self._coordinator.enqueue_deferred(envelope)
            else:
                self._coordinator.enqueue(envelope)
            await self._process_through_coordinator()
            await self._record_incoming_external_id(envelope.external_id)
            await self._drain_deferred_queue()

    async def _handle_callback_query(self, callback: CallbackQuery) -> None:
        """Handle an inline button tap (CallbackQuery).

        Ordering follows the design's Tap routing sequence:
        answer → auth → detached keyboard removal → unpack → envelope → lock branching.
        """
        # 1. Acknowledge first — clears the originator's spinner regardless of agent state.
        try:
            await callback.answer()
        except Exception:
            _log.exception("callback_query.answer() failed; continuing")

        # 2. In-handler auth (not router filter — see KD-6).
        if callback.from_user.id != self._settings.authorized_chat_id:
            _log.debug("Dropping unauthorized tap from user={uid}", uid=callback.from_user.id)
            return

        # 3. Validate callback data.
        if callback.data is None:
            return

        # 4. Unpack the callback_data to recover (value, single_use).
        try:
            value, single_use = _unpack(callback.data)
        except ValueError:
            _log.warning("Unrecognized callback_data: {data!r}", data=callback.data)
            return

        # 5. Schedule detached keyboard removal when single-use and message is present.
        if single_use and callback.message is not None:
            task = asyncio.create_task(
                self._remove_keyboard(callback.message.chat.id, callback.message.message_id),
            )
            self._delivery_tasks.add(task)
            task.add_done_callback(self._delivery_tasks.discard)

        # 6. Build envelope.
        tap = ButtonTapMessage(value=value)

        # 7. DES-009 lock branching (mirrors _handle_message / _handle_media).
        if self._delivery_lock.locked():
            self._coordinator.enqueue(tap)
            return

        async with self._delivery_lock:
            self._coordinator.enqueue(tap)
            await self._process_through_coordinator()
            await self._drain_deferred_queue()

    async def _handle_reaction(self, event: MessageReactionUpdated) -> None:
        """Handle a message_reaction update from the authorized user.

        Filters emoji-only entries from old/new reactions, computes the diff,
        and routes through the same lock branching as other handlers.

        When the reacted-to message maps to a different session and the
        channel is idle, the reaction is deferred with target_session_id set
        for session-switching routing (DES-009, R4).
        """
        new_emojis = _emoji_set(event.new_reaction)
        old_emojis = _emoji_set(event.old_reaction)
        added = new_emojis - old_emojis
        removed = old_emojis - new_emojis

        if not added and not removed:
            _log.debug("Reaction update with no interpretable change; dropping")
            return

        envelope = ReactionMessage(added=added, removed=removed, external_id=str(event.message_id))

        # Mid-stream: steer into current session (DES-009, R4 AC)
        if self._delivery_lock.locked():
            _log.debug("Mid-stream steering reaction")
            self._coordinator.enqueue(envelope)
            return

        # Idle path: check if reaction maps to a different session
        target_session_id = await self._resolve_reply_target(str(event.message_id))
        if target_session_id is not None:
            _log.debug(
                "Reaction maps to different session: target={target}",
                target=target_session_id,
            )

        async with self._delivery_lock:
            if target_session_id is not None:
                envelope = replace(envelope, target_session_id=target_session_id)
                _log.debug(
                    "Deferring reaction for session switch: target={target}",
                    target=target_session_id,
                )
                self._coordinator.enqueue_deferred(envelope)
            else:
                self._coordinator.enqueue(envelope)
            await self._process_through_coordinator()
            await self._drain_deferred_queue()

    async def _remove_keyboard(self, chat_id: int, message_id: int) -> None:
        """Remove the inline keyboard from a message (detached task).

        Log-and-swallow errors. "message is not modified" is treated as no-op.
        """
        try:
            await self._bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                _log.debug(
                    "Keyboard already removed: chat={chat} msg={msg}",
                    chat=chat_id,
                    msg=message_id,
                )
            else:
                _log.warning(
                    "Failed to remove keyboard: chat={chat} msg={msg}: {err}",
                    chat=chat_id,
                    msg=message_id,
                    err=e,
                )
        except TelegramAPIError:
            _log.warning(
                "Failed to remove keyboard: chat={chat} msg={msg}",
                chat=chat_id,
                msg=message_id,
                exc_info=True,
            )

    async def _send_shutdown_status(self, message: str) -> None:
        """Send or update a dedicated shutdown progress message."""
        chat_id = self._settings.authorized_chat_id
        try:
            if self._shutdown_message_id is None:
                msg = await self._bot.send_message(chat_id, f"_{message}_", parse_mode="Markdown")
                self._shutdown_message_id = msg.message_id
            else:
                await self._bot.edit_message_text(
                    f"_{message}_",
                    chat_id=chat_id,
                    message_id=self._shutdown_message_id,
                    parse_mode="Markdown",
                )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                _log.debug("Shutdown status edit skipped: message content unchanged")
            else:
                _log.exception("Failed to update shutdown status message")
        except TelegramAPIError:
            _log.exception("Failed to update shutdown status message")

    async def close(self) -> None:
        """Close the bot's HTTP session.

        Called from the main entry point after coordinator cleanup (including
        post-processing) has finished. The bot session is intentionally kept
        open past polling teardown so that shutdown status messages can still
        be sent during the coordinator's ``__aexit__``.
        """
        try:
            await self._bot.session.close()
        except Exception:
            _log.exception("Failed to close bot session")

    async def _on_shutdown(self) -> None:
        """Send partial response on shutdown if one is active."""
        if self._active_renderer is not None and self._active_renderer._buffer:
            _log.info("Sending partial response before shutdown")
            try:
                await self._active_renderer.finalize()
            except TelegramAPIError:
                _log.warning("Could not send partial response on shutdown")

    async def _handle_buffered_delivery(self, event: BufferedDelivery) -> None:
        """Handle a BufferedDelivery event from the priority buffer.

        Spawns a detached task for the delivery work so the EventBus is freed
        immediately. The task acquires the delivery lock and processes through
        the coordinator pipeline.
        """
        _log.info(
            "Spawning delivery task: items={count}, shutdown={is_shutdown}",
            count=len(event.items),
            is_shutdown=event.is_shutdown_digest,
        )

        task = asyncio.create_task(self._deliver(event))

        self._delivery_tasks.add(task)
        task.add_done_callback(self._delivery_tasks.discard)

    async def _deliver(self, event: BufferedDelivery) -> None:
        """Execute a buffered delivery through the coordinator pipeline.

        Runs as a detached task spawned by _handle_buffered_delivery.
        """
        try:
            async with self._delivery_lock:
                self._coordinator.enqueue(
                    TextMessage(text=event.prompt, pinned_skills=event.pinned_skills())
                )
                await self._process_through_coordinator(on_complete=self._build_on_complete(event))
                await self._drain_deferred_queue()
        except Exception:
            _log.exception(
                "Error in detached delivery task: items={count}, shutdown={is_shutdown}",
                count=len(event.items),
                is_shutdown=event.is_shutdown_digest,
            )
        finally:
            if event.is_shutdown_digest and self._buffer is not None:
                self._buffer.resolve_shutdown()

    def _build_on_complete(self, event: BufferedDelivery) -> Callable[[], Awaitable[None]]:
        """Build the on_complete callback for a buffered delivery."""

        async def on_complete() -> None:
            for item in event.items:
                if item.on_delivered is not None:
                    await item.on_delivered()

        return on_complete

    async def _record_outgoing_ids(self, session_id: str, message_ids: list[int]) -> None:
        """Record outgoing message IDs to the session's external ID table.

        Best-effort: each ID is recorded independently so one failure
        doesn't block the rest.
        """
        registry = self._coordinator._registry
        if registry is None:
            return

        for mid in message_ids:
            try:
                await registry.record_channel_message(session_id, "telegram", "outgoing", str(mid))
            except Exception:
                _log.warning(
                    "Failed to record outgoing external ID (best-effort): "
                    "session_id={sid} eid={eid}",
                    sid=session_id,
                    eid=mid,
                    exc_info=True,
                )

    async def _process_through_coordinator(
        self,
        on_complete: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Process one buffered message through the coordinator.

        Serialization is provided by the caller's _delivery_lock. Follow-up
        messages queue on the lock and become the next call.
        """
        chat_id = self._settings.authorized_chat_id
        self._active_renderer = ResponseRenderer(
            self._bot,
            chat_id,
            push_notifications=self._settings.push_notifications,
            is_pinned=self._is_pinned,
        )

        # Capture session_id before processing for outgoing ID recording
        session_id: str | None = None
        registry = self._coordinator._registry
        if registry is not None:
            try:
                active_session = await registry.get_active_session()
                session_id = active_session.id if active_session else None
            except Exception:
                _log.debug("Failed to capture session_id for outgoing ID recording")

        try:
            async with ChatActionSender(bot=self._bot, chat_id=chat_id, action="typing"):
                # Coordinator's re-queue loop handles leftover messages internally
                async for event in self._coordinator.send_message():
                    if isinstance(event, Status):
                        await self._active_renderer.handle_status(event.message)
                    elif isinstance(event, TextChunk):
                        await self._active_renderer.handle_text(event.text)
                    elif isinstance(event, ToolActivity):
                        await self._active_renderer.handle_tool(event)
                    elif isinstance(event, Error):
                        await self._active_renderer.handle_error(event)
                    elif isinstance(event, Result):
                        # Capture outgoing IDs before reset
                        outgoing_ids = self._active_renderer.get_finalized_message_ids()
                        await self._active_renderer.finalize()
                        await self._active_renderer.notify()
                        self._active_renderer.reset()

                        # Record outgoing IDs asynchronously
                        if session_id is not None and outgoing_ids:
                            task = asyncio.create_task(
                                self._record_outgoing_ids(session_id, outgoing_ids)
                            )
                            self._delivery_tasks.add(task)
                            task.add_done_callback(self._delivery_tasks.discard)

            if on_complete is not None:
                await on_complete()

        except Exception as e:
            _log.exception("Error during message processing")

            had_content = (
                self._active_renderer is not None and self._active_renderer.has_sent_content()
            )

            if had_content:
                with contextlib.suppress(TelegramAPIError):
                    await self._active_renderer.notify()

            with contextlib.suppress(TelegramAPIError):
                await self._bot.send_message(
                    chat_id,
                    sanitize_text(f"⚠️ Error: {e!s}"),
                    parse_mode=None,
                    disable_notification=had_content,
                )

        finally:
            self._active_renderer = None


async def telegram_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook for Telegram channel initialization.

    This hook:
    - Skips if channel is not "telegram"
    - Prompts for bot_token and authorized_chat_id if missing
    - Validates the bot token by calling get_me()
    - Retries on transient network errors

    Per DES-003, this hook is defined in the telegram module
    and registered in __main__.py.
    """
    settings = ctx.settings_manager.settings

    # Self-skip when not telegram channel
    if settings.channel != "telegram":
        _log.debug("Skipping telegram_hook: channel={ch}", ch=settings.channel)
        return

    # Check if telegram config exists
    if settings.telegram is None:
        # Prompt for configuration
        _log.info("Telegram configuration required")

        bot_token = ctx.prompt("Enter your Telegram bot token (from @BotFather): ").strip()
        if not bot_token:
            raise BootstrapError("Bot token is required")

        chat_id_str = ctx.prompt(
            "Enter your Telegram chat ID "
            "(send /start to your bot, then check "
            "https://api.telegram.org/bot<TOKEN>/getUpdates): "
        ).strip()
        if not chat_id_str:
            raise BootstrapError("Chat ID is required")

        try:
            chat_id = int(chat_id_str)
        except ValueError:
            raise BootstrapError(f"Invalid chat ID: {chat_id_str}") from None

        # Persist configuration
        ctx.settings_manager.update("telegram", "bot_token", bot_token)
        ctx.settings_manager.update("telegram", "authorized_chat_id", chat_id)
        ctx.settings_manager.save()

        _log.info("Telegram configuration saved")

    # Validate token with retry
    telegram_settings = ctx.settings_manager.settings.telegram
    if telegram_settings is None:
        raise BootstrapError("Telegram configuration not available after save")

    bot = Bot(token=telegram_settings.bot_token)
    max_retries = 3
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            me = await bot.get_me()
            _log.info(
                "Telegram bot validated: @{username}",
                username=me.username or "unknown",
            )
            return

        except TelegramAPIError as e:
            # Check for auth error (invalid token)
            error_text = str(e).lower()
            if "unauthorized" in error_text or "invalid" in error_text:
                raise BootstrapError(f"Invalid bot token: {e}") from e

            # Transient error - retry with backoff
            if attempt < max_retries - 1:
                _log.warning(
                    "Telegram API error (attempt {n}/{max}), retrying in {s}s: {err}",
                    n=attempt + 1,
                    max=max_retries,
                    s=retry_delay,
                    err=e,
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise BootstrapError(
                    f"Telegram API unreachable after {max_retries} retries: {e}"
                ) from e

        finally:
            # Always close the bot session
            await bot.session.close()
