"""present_buttons MCP tool server for presenting Telegram inline keyboards.

Follows DES-006: factory pattern with closure-captured state,
extracted handler for testability, Pydantic model for arg validation.
Array-typed ``buttons`` arg uses the JSON-string pattern per DES-006.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from pydantic import BaseModel

_MAX_VALUE_BYTES = 58
_MAX_BUTTON_COUNT = 100
_SINGLE_USE_PREFIX = "btn1:"
_MULTI_USE_PREFIX = "btnN:"


class Button(BaseModel):
    """A single button with a visible label and a machine-readable value."""

    label: str
    value: str


class PresentButtonsArgs(BaseModel):
    """Arguments for the present_buttons tool."""

    prompt: str
    buttons: str
    single_use: bool = True


def _decode_buttons(raw: str) -> list[list[Button]]:
    """Parse the JSON-string ``buttons`` argument into validated rows.

    Raises:
        ValueError: On any structural or per-button validation failure.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"buttons must be a JSON-encoded list of rows: {exc}") from exc

    if not isinstance(decoded, list) or len(decoded) == 0:
        raise ValueError("buttons must be a non-empty list of rows")

    total = 0
    rows: list[list[Button]] = []
    for row_idx, raw_row in enumerate(decoded):
        if not isinstance(raw_row, list) or len(raw_row) == 0:
            raise ValueError(f"row {row_idx} must be a non-empty list of buttons")
        parsed_row: list[Button] = []
        for btn_idx, raw_btn in enumerate(raw_row):
            if not isinstance(raw_btn, dict):
                raise ValueError(f"button at row {row_idx} index {btn_idx} must be an object")
            btn = Button.model_validate(raw_btn)
            if not btn.label.strip():
                raise ValueError(f"button at row {row_idx} index {btn_idx} has an empty label")
            if not btn.value:
                raise ValueError(f"button at row {row_idx} index {btn_idx} has an empty value")
            val_bytes = len(btn.value.encode("utf-8"))
            if val_bytes > _MAX_VALUE_BYTES:
                raise ValueError(
                    f"Button value '{btn.value[:20]}…' exceeds "
                    f"{_MAX_VALUE_BYTES}-byte limit ({val_bytes} bytes)"
                )
            parsed_row.append(btn)
            total += 1
        rows.append(parsed_row)

    if total > _MAX_BUTTON_COUNT:
        raise ValueError(f"Total button count ({total}) exceeds cap ({_MAX_BUTTON_COUNT})")

    return rows


def _pack(value: str, single_use: bool) -> str:
    """Encode a button value and single-use flag into ``callback_data``."""
    prefix = _SINGLE_USE_PREFIX if single_use else _MULTI_USE_PREFIX
    return f"{prefix}{value}"


def _unpack(data: str) -> tuple[str, bool]:
    """Decode ``callback_data`` back into (value, single_use).

    Raises:
        ValueError: If the prefix is unrecognized.
    """
    if data.startswith(_SINGLE_USE_PREFIX):
        return data[len(_SINGLE_USE_PREFIX) :], True
    if data.startswith(_MULTI_USE_PREFIX):
        return data[len(_MULTI_USE_PREFIX) :], False
    raise ValueError(f"Unrecognized callback_data prefix in: {data!r}")


_TOOL_DESCRIPTION = (
    "Present a Telegram inline keyboard of tappable buttons in the active chat.\n"
    "\n"
    "Parameters:\n"
    "- prompt (str, required): The message text shown above the buttons.\n"
    "- buttons (str, required): A JSON-encoded list of rows. Each row is a list of\n"
    "  button objects with {label, value}. label is shown on the button; value is\n"
    "  the machine-readable identifier you receive back on tap.\n"
    '  Example: \'[[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],\n'
    '            [{"label": "Cancel", "value": "cancel"}]]\'\n'
    "- single_use (bool, optional, default True): If true, the keyboard is removed\n"
    "  from the message after any button is tapped. If false, the keyboard remains\n"
    "  attached and further taps from the same message are routed normally.\n"
    "\n"
    "Returns the sent message's Telegram ID on success, or an error result on\n"
    "validation/API failure. Per-button value must be ≤ 58 UTF-8 bytes; per-button\n"
    "label must be non-empty; at least one row with at least one button is required.\n"
    "\n"
    "When the user taps a button, you will receive a turn explicitly framed as\n"
    '"The user tapped the option `<value>` out of the options you displayed.",\n'
    "so you can distinguish taps from typed input. Use this for structured\n"
    "prompts like yes/no, multiple-choice, or confirm/cancel."
)


async def handle_present_buttons(
    prompt: str,
    buttons_raw: str,
    single_use: bool,
    bot: Bot,
    chat_id: int,
) -> dict:
    """Handle a present_buttons tool call.

    Builds an inline keyboard from the agent's button definitions and sends it
    as a Telegram message. Returns the message ID on success, or an error dict
    on validation or API failure.
    """
    try:
        decoded = _decode_buttons(buttons_raw)
    except ValueError as e:
        return {"is_error": True, "content": [{"type": "text", "text": str(e)}]}

    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=b.label,
                    callback_data=_pack(b.value, single_use),
                )
                for b in row
            ]
            for row in decoded
        ]
    )

    try:
        sent = await bot.send_message(chat_id, prompt, reply_markup=markup)
    except TelegramAPIError as e:
        return {"is_error": True, "content": [{"type": "text", "text": str(e)}]}

    return {"content": [{"type": "text", "text": f"Buttons sent (message_id: {sent.message_id})"}]}


def create_buttons_server(
    bot: Bot,
    chat_id: int,
    mark_sent: Callable[[], None] | None = None,
) -> McpSdkServerConfig:
    """Create MCP tool server for the present_buttons tool.

    Args:
        bot: The aiogram Bot instance.
        chat_id: The Telegram chat ID.
        mark_sent: Optional callback invoked when buttons are successfully sent.

    Returns:
        McpSdkServerConfig for registration with the coordinator.
    """

    @tool(
        "present_buttons",
        _TOOL_DESCRIPTION,
        PresentButtonsArgs.model_json_schema(),
    )
    async def present_buttons(args: dict) -> dict:
        parsed = PresentButtonsArgs.model_validate(args)
        result = await handle_present_buttons(
            parsed.prompt,
            parsed.buttons,
            parsed.single_use,
            bot,
            chat_id,
        )
        if not result.get("is_error") and mark_sent is not None:
            mark_sent()
        return result

    return create_sdk_mcp_server(
        name="telegram-buttons",
        tools=[present_buttons],
    )
