"""Pinning MCP tool server for pinning/unpinning Telegram messages.

Follows DES-006: factory pattern with closure-captured state,
extracted handler for testability, Pydantic model for arg validation.
"""

from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel


class UnpinMessageArgs(BaseModel):
    """Arguments for the unpin_message tool."""

    message_id: int


async def handle_pin_message(
    getter: Callable[[], int | None],
    bot: Bot,
    chat_id: int,
) -> dict:
    """Handle a pin_message tool call.

    Pins the last response message and returns its ID. The pin triggers a
    push notification so the user sees the pinned message promptly.
    Returns an error if no message is available or pinning fails.
    """
    message_id = getter()

    if message_id is None:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "No message available to pin"}],
        }

    try:
        await bot.pin_chat_message(chat_id, message_id, disable_notification=False)
    except TelegramAPIError as e:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": f"Failed to pin message: {e}"}],
        }

    return {
        "content": [{"type": "text", "text": f"Message pinned (ID: {message_id})"}],
    }


async def handle_unpin_message(
    message_id: int,
    bot: Bot,
    chat_id: int,
) -> dict:
    """Handle an unpin_message tool call.

    Unpins the specified message. Returns an error on API failure.
    """
    try:
        await bot.unpin_chat_message(chat_id, message_id=message_id)
    except TelegramAPIError as e:
        return {
            "is_error": True,
            "content": [{"type": "text", "text": f"Failed to unpin message: {e}"}],
        }

    return {
        "content": [{"type": "text", "text": f"Message unpinned (ID: {message_id})"}],
    }


def create_pinning_server(
    bot: Bot,
    chat_id: int,
    get_last_message_id: Callable[[], int | None],
) -> tuple[McpSdkServerConfig, Callable[[int], bool]]:
    """Create MCP tool server for pin_message and unpin_message tools.

    Args:
        bot: The aiogram Bot instance.
        chat_id: The Telegram chat ID.
        get_last_message_id: Callable returning the current renderer's last message ID.

    Returns:
        Tuple of (McpSdkServerConfig, is_pinned checker). The checker returns
        True for message IDs that are currently tracked as pinned.
    """
    pinned_ids: set[int] = set()

    @tool(
        "pin_message",
        "Pin a message in the active Telegram chat.\n"
        "\n"
        "Parameters: none\n"
        "\n"
        "Pins the most recent response message. The pin triggers a push "
        "notification so the user sees the pinned message promptly. "
        "Returns the pinned message's Telegram ID on success. "
        "Returns an error if no response has been sent yet or if pinning fails "
        "(e.g., insufficient permissions in groups/channels).\n"
        "\n"
        "Idempotent: pinning an already-pinned message succeeds.",
        {},
    )
    async def pin_message(args: dict) -> dict:
        result = await handle_pin_message(get_last_message_id, bot, chat_id)
        if not result.get("is_error"):
            msg_id = get_last_message_id()
            if msg_id is not None:
                pinned_ids.add(msg_id)
        return result

    @tool(
        "unpin_message",
        "Unpin a previously pinned message in the active Telegram chat.\n"
        "\n"
        "Parameters:\n"
        "- message_id (int, required): The Telegram message ID to unpin\n"
        "\n"
        "Returns success on unpin. Returns an error if the message ID doesn't "
        "exist or unpinning fails.\n"
        "\n"
        "Idempotent: unpinning a non-pinned message succeeds.",
        UnpinMessageArgs.model_json_schema(),
    )
    async def unpin_message(args: dict) -> dict:
        parsed = UnpinMessageArgs.model_validate(args)
        result = await handle_unpin_message(parsed.message_id, bot, chat_id)
        if not result.get("is_error"):
            pinned_ids.discard(parsed.message_id)
        return result

    def is_pinned(message_id: int) -> bool:
        return message_id in pinned_ids

    server = create_sdk_mcp_server(
        name="telegram-pinning",
        tools=[pin_message, unpin_message],
    )

    return server, is_pinned
