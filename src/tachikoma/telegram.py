"""Telegram channel: bot communication and response rendering.

This module provides the Telegram bot integration for Tachikoma,
including progressive message editing, tool activity display,
and streaming response rendering.
"""

import asyncio
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
from loguru import logger
from telegramify_markdown import convert

from tachikoma.bootstrap import BootstrapContext, BootstrapError
from tachikoma.config import TelegramSettings
from tachikoma.coordinator import Coordinator
from tachikoma.display import TOOL_DISPLAY
from tachikoma.events import Error, Result, TextChunk, ToolActivity

_log = logger.bind(component="telegram")

# Telegram message size limit with safety margin
MAX_MESSAGE_SIZE = 3800

# Time-based throttle interval for edits (seconds)
EDIT_THROTTLE_INTERVAL = 2.0


class ResponseRenderer:
    """Renders agent events as Telegram messages with progressive editing.

    The renderer accumulates text in a buffer and periodically edits a single
    Telegram message to show the streaming response. Tool activity appears as
    an inline status line within the message.
    """

    def __init__(self, bot: Bot, chat_id: int) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._current_message_id: int | None = None
        self._buffer: str = ""
        self._tool_line: str | None = None
        self._had_tools: bool = False
        self._tools_marker_inserted: bool = False
        self._last_edit_time: float = 0.0
        self._message_count: int = 0

    def reset(self) -> None:
        """Clear all state for a new response.

        Called after each Result event to prepare for the next turn
        (e.g., when steering is active).
        """
        self._current_message_id = None
        self._buffer = ""
        self._tool_line = None
        self._had_tools = False
        self._tools_marker_inserted = False
        self._last_edit_time = 0.0
        # Note: _message_count is NOT reset - it tracks total messages
        # for the entire response cycle including steered messages

    async def handle_text(self, chunk: str) -> None:
        """Handle a TextChunk event by appending to buffer and scheduling edit."""
        # If we had tools and this is the first text after them,
        # insert the "Ran tools" marker
        if self._had_tools and not self._tools_marker_inserted:
            self._buffer += "_🔧 Ran tools_\n"
            self._tools_marker_inserted = True
            self._tool_line = None  # Clear tool line - marker replaces it

        self._buffer += chunk
        await self._flush(force=False)

    async def handle_tool(self, activity: ToolActivity) -> None:
        """Handle a ToolActivity event by setting the tool line."""
        display_fn = TOOL_DISPLAY.get(activity.tool_name)
        label = (
            display_fn(activity.tool_input)
            if display_fn
            else f"{activity.tool_name}..."
        )
        self._tool_line = f"_{label}_"
        self._had_tools = True
        await self._flush(force=False)

    async def handle_error(self, error: Error) -> None:
        """Handle an Error event by sending a separate error message."""
        error_text = f"⚠️ Error: {error.message}"

        try:
            await self._bot.send_message(
                self._chat_id,
                error_text,
                parse_mode=None,
            )
        except TelegramAPIError:
            _log.exception("Failed to send error message")

        if not error.recoverable:
            _log.error("Non-recoverable error: {msg}", msg=error.message)

    async def finalize(self) -> None:
        """Send the final state of the current message, bypassing throttle."""
        await self._flush(force=True)

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
                display_text += f"\n{self._tool_line}"
            else:
                display_text = self._tool_line

        # Handle empty state (nothing to send yet)
        if not display_text:
            return

        # Check for message splitting before formatting
        if len(display_text) > MAX_MESSAGE_SIZE:
            await self._split_and_send(display_text)
            return

        # Convert markdown to Telegram entities format
        text, entities = convert(display_text)

        try:
            if self._current_message_id is None:
                # Send new message
                msg = await self._bot.send_message(
                    self._chat_id,
                    text,
                    parse_mode=None,
                    entities=[e.to_dict() for e in entities],
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
                    text,
                    self._chat_id,
                    self._current_message_id,
                    parse_mode=None,
                    entities=[e.to_dict() for e in entities],
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

        except TelegramAPIError:
            # Other API errors - log and skip this edit
            _log.exception("Failed to send/edit message")

    async def _split_and_send(self, display_text: str) -> None:
        """Split text at paragraph boundary and send as multiple messages."""
        # Find the best split point
        split_pos = self._find_split_position(display_text)

        # Split the text
        first_part = display_text[:split_pos].rstrip()
        remainder = display_text[split_pos:].lstrip()

        # Finalize current message with first part
        self._buffer = first_part
        self._tool_line = None  # Tool line goes with remainder

        # Send first part (force to bypass throttle)
        await self._flush(force=True)

        # Start new message with remainder
        self._current_message_id = None
        self._buffer = remainder

        # If remainder is still too long, recursively split
        if len(remainder) > MAX_MESSAGE_SIZE:
            await self._split_and_send(remainder)
        else:
            await self._flush(force=True)

    def _find_split_position(self, text: str) -> int:
        """Find the best position to split text, preferring paragraph boundaries."""
        # Look for paragraph boundary (\n\n) before the limit
        paragraph_split = text.rfind("\n\n", 0, MAX_MESSAGE_SIZE)
        if paragraph_split > 0:
            return paragraph_split + 2  # Include one newline

        # Fall back to single newline
        newline_split = text.rfind("\n", 0, MAX_MESSAGE_SIZE)
        if newline_split > 0:
            return newline_split + 1

        # Hard split at limit
        return MAX_MESSAGE_SIZE


class TelegramChannel:
    """Telegram bot channel that receives messages and renders agent responses.

    The channel uses aiogram for long polling and message handling.
    It supports steering (injecting mid-stream messages) and graceful
    shutdown with partial response delivery.
    """

    def __init__(self, coordinator: Coordinator, settings: TelegramSettings) -> None:
        self._coordinator = coordinator
        self._settings = settings
        self._bot = Bot(token=settings.bot_token)
        self._dispatcher = Dispatcher()
        self._router = Router()
        self._active_renderer: ResponseRenderer | None = None
        self._is_processing: bool = False

        # Set up router with authorization filter
        self._router.message.filter(F.chat.id == settings.authorized_chat_id)

        # Register message handler
        self._router.message(F.text)(self._handle_message)

        # Include router in dispatcher
        self._dispatcher.include_router(self._router)

        # Register shutdown hook
        self._dispatcher.shutdown.register(self._on_shutdown)

    async def run(self) -> None:
        """Start the bot and begin polling for messages.

        This method blocks until the bot is stopped (via signal or error).
        """
        _log.info(
            "Starting Telegram bot for chat {chat_id}",
            chat_id=self._settings.authorized_chat_id,
        )

        # aiogram handles SIGTERM/SIGINT internally
        await self._dispatcher.start_polling(
            self._bot,
            handle_signals=True,
            backoff_config={
                "min_delay": 1,
                "max_delay": 60,
                "multiplier": 2,
            },
        )

    async def _handle_message(self, message: Message) -> None:
        """Handle an incoming message from the authorized user."""
        # Validate message has text
        if not message.text or not message.text.strip():
            _log.debug("Ignoring empty or non-text message")
            return

        text = message.text.strip()
        chat_id = message.chat.id

        # Check if we're already processing (steering case)
        if self._is_processing:
            _log.debug("Steering mid-stream message")
            await self._coordinator.steer(text)
            return

        # Start processing
        self._is_processing = True
        self._active_renderer = ResponseRenderer(self._bot, chat_id)

        try:
            async with ChatActionSender(bot=self._bot, chat_id=chat_id, action="typing"):
                async for event in self._coordinator.send_message(text):
                    if isinstance(event, TextChunk):
                        await self._active_renderer.handle_text(event.text)
                    elif isinstance(event, ToolActivity):
                        await self._active_renderer.handle_tool(event)
                    elif isinstance(event, Error):
                        await self._active_renderer.handle_error(event)
                    elif isinstance(event, Result):
                        await self._active_renderer.finalize()
                        self._active_renderer.reset()

        except Exception as e:
            _log.exception("Error during message processing")
            # Try to send error message
            try:
                await self._bot.send_message(
                    chat_id,
                    f"⚠️ Error: {e!s}",
                    parse_mode=None,
                )
            except TelegramAPIError:
                pass

        finally:
            self._is_processing = False
            self._active_renderer = None

    async def _on_shutdown(self) -> None:
        """Send partial response on shutdown if one is active."""
        if self._active_renderer is not None and self._active_renderer._buffer:
            _log.info("Sending partial response before shutdown")
            try:
                await self._active_renderer.finalize()
            except TelegramAPIError:
                # Bot session may be closing - log and continue
                _log.warning("Could not send partial response on shutdown")


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

