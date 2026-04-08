"""Telegram channel: bot communication and response rendering.

This module provides the Telegram bot integration for Tachikoma,
including progressive message editing, tool activity display,
and streaming response rendering.
"""

import asyncio
import contextlib
import signal
import sys
import termios
import time
import tty
from collections.abc import Awaitable, Callable
from os.path import basename
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.dispatcher import BackoffConfig
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from bubus import EventBus
from loguru import logger
from telegramify_markdown import convert, split_entities, utf16_len

from tachikoma.adapter import sanitize_text
from tachikoma.bootstrap import BootstrapContext, BootstrapError
from tachikoma.config import TelegramSettings
from tachikoma.coordinator import Coordinator
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
from tachikoma.notifications import Notification
from tachikoma.tasks.events import SessionTaskReady

_log = logger.bind(component="telegram")

# Telegram hard limit in UTF-16 code units
TELEGRAM_MAX_UTF16 = 4096

# Time-based throttle interval for edits (seconds)
EDIT_THROTTLE_INTERVAL = 2.0


def code_wrap(text: str) -> str:
    """Wrap text in markdown inline code span, handling backtick collision.

    Single backticks for normal text; double backticks with space padding
    when content contains backticks (per CommonMark 6.1).
    """
    if "`" not in text:
        return f"`{text}`"

    return f"`` {text} ``"


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
        f"globbing {code_wrap(inp['pattern'])}"
        if "pattern" in inp
        else "globbing a pattern"
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

    def __init__(self, bot: Bot, chat_id: int, push_notifications: bool = False) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._push_notifications = push_notifications
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

    async def handle_status(self, message: str) -> None:
        """Handle a Status event by sending a transient status message.

        The message will be replaced when the first TextChunk or ToolActivity arrives.
        """
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
                self._tool_activities, summary_map=TELEGRAM_TOOL_SUMMARY,
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
                self._tool_activities, summary_map=TELEGRAM_TOOL_SUMMARY,
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
                        "Failed to delete original after copy (duplicate visible): "
                        "message_id={id}",
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
        for old_id in old_ids[len(chunks):]:
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


class TelegramChannel:
    """Telegram bot channel that receives messages and renders agent responses.

    The channel uses aiogram for long polling and message handling.
    Mid-stream messages are buffered via the coordinator's ``enqueue()``
    and processed after the current response completes.  Supports graceful
    shutdown with partial response delivery.

    When an event bus is provided, the channel subscribes to:
    - SessionTaskReady: Proactive tasks from the task scheduler
    - Notification: Completion/failure notifications from background tasks
    """

    def __init__(
        self,
        coordinator: Coordinator,
        settings: TelegramSettings,
        bus: EventBus | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._settings = settings
        self._bot = Bot(token=settings.bot_token)
        self._dispatcher = Dispatcher()
        self._router = Router()
        self._active_renderer: ResponseRenderer | None = None
        self._is_processing: bool = False
        self._bus = bus

        # Set up router with authorization filter
        self._router.message.filter(F.chat.id == settings.authorized_chat_id)

        # Register message handler
        self._router.message(F.text)(self._handle_message)

        # Register media handler (catch-all for all supported media types)
        self._router.message(
            F.photo | F.voice | F.audio
            | F.document | F.sticker | F.video
            | F.video_note | F.animation,
        )(self._handle_media)

        # Include router in dispatcher
        self._dispatcher.include_router(self._router)

        # Register shutdown hook
        self._dispatcher.shutdown.register(self._on_shutdown)

        # Subscribe to task events if bus is provided
        if self._bus is not None:
            self._bus.on(SessionTaskReady, self._handle_session_task)
            self._bus.on(Notification, self._handle_notification)

    async def run(self) -> None:
        """Start the bot and begin polling for messages.

        This method blocks until the bot is stopped (via signal, 'q' keypress,
        or error). Signals are handled manually (not by aiogram) so that
        polling stops gracefully without cancelling the task — this allows the
        Coordinator's post-processing pipeline to run on shutdown.
        """
        _log.info(
            "Starting Telegram bot for chat {chat_id}",
            chat_id=self._settings.authorized_chat_id,
        )

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

            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.remove_signal_handler(sig)

    async def _handle_message(self, message: Message) -> None:
        """Handle an incoming message from the authorized user."""
        if not message.text or not message.text.strip():
            _log.debug("Ignoring empty or non-text message")
            return

        text = message.text.strip()
        self._coordinator.enqueue(text)

        if self._is_processing:
            _log.debug("Buffered mid-stream message")
            return

        await self._process_through_coordinator()

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
            descriptor.label, metadata_lines, dest_path, message.caption,
        )

        self._coordinator.enqueue(description)

        if self._is_processing:
            _log.debug("Buffered mid-stream media message")
            return

        await self._process_through_coordinator()

    async def _on_shutdown(self) -> None:
        """Send partial response on shutdown if one is active."""
        if self._active_renderer is not None and self._active_renderer._buffer:
            _log.info("Sending partial response before shutdown")
            try:
                await self._active_renderer.finalize()
            except TelegramAPIError:
                _log.warning("Could not send partial response on shutdown")

    async def _handle_session_task(self, event: SessionTaskReady) -> None:
        """Handle a SessionTaskReady event from the task scheduler.

        This delivers a proactive task message to the user through the coordinator.
        If the user is currently in a conversation, the message is buffered.
        """
        instance = event.instance
        _log.info(
            "Processing session task: id={task_id}, prompt_preview={preview}",
            task_id=instance.id,
            preview=instance.prompt[:50] if instance.prompt else "",
        )

        self._coordinator.enqueue(instance.prompt)

        if self._is_processing:
            _log.debug("Buffered session task mid-stream")
            return

        await self._process_through_coordinator(on_complete=event.on_complete)

    async def _process_through_coordinator(
        self,
        on_complete: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Process buffered messages through the coordinator and render responses.

        Drains the coordinator's message buffer in a loop.  Each iteration
        calls ``send_message()`` which runs the full pipeline (boundary
        detection, pre-processing, SDK session).  The loop continues until
        the buffer is empty.

        Args:
            on_complete: Optional callback invoked after the buffer is drained.
        """
        chat_id = self._settings.authorized_chat_id
        self._is_processing = True
        self._active_renderer = ResponseRenderer(
            self._bot, chat_id, push_notifications=self._settings.push_notifications
        )

        try:
            async with ChatActionSender(bot=self._bot, chat_id=chat_id, action="typing"):
                while self._coordinator.has_pending_messages:
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
                            await self._active_renderer.finalize()
                            await self._active_renderer.notify()
                            self._active_renderer.reset()

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
            self._is_processing = False
            self._active_renderer = None

    async def _handle_notification(self, event: Notification) -> None:
        """Handle a Notification event from the background task executor.

        Routes the notification prompt through the coordinator pipeline,
        following the same delivery pattern as session tasks.
        """
        _log.info(
            "Routing notification: severity={severity}, source={source}",
            severity=event.severity,
            source=event.source_id,
        )

        self._coordinator.enqueue(event.prompt)

        if self._is_processing:
            _log.debug("Buffered task notification mid-stream")
            return

        await self._process_through_coordinator()


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
