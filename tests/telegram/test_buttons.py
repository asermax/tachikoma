"""Tests for the buttons tool server (telegram/buttons.py).

Inline keyboard presentation, callback_data pack/unpack, and input validation.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramAPIError

from tachikoma.telegram.buttons import (
    _MAX_BUTTON_COUNT,
    _MAX_VALUE_BYTES,
    _decode_buttons,
    _pack,
    _unpack,
    create_buttons_server,
    handle_present_buttons,
)


class TestPack:
    """Tests for the _pack helper."""

    def test_single_use_prefix(self) -> None:
        assert _pack("yes", True) == "btn1:yes"

    def test_multi_use_prefix(self) -> None:
        assert _pack("no", False) == "btnN:no"

    def test_value_with_colon(self) -> None:
        assert _pack("a:b", True) == "btn1:a:b"


class TestUnpack:
    """Tests for the _unpack helper."""

    def test_single_use_roundtrip(self) -> None:
        value, single_use = _unpack("btn1:approve")
        assert value == "approve"
        assert single_use is True

    def test_multi_use_roundtrip(self) -> None:
        value, single_use = _unpack("btnN:cancel")
        assert value == "cancel"
        assert single_use is False

    def test_roundtrip_single_use(self) -> None:
        packed = _pack("yes", True)
        value, single_use = _unpack(packed)
        assert value == "yes"
        assert single_use is True

    def test_roundtrip_multi_use(self) -> None:
        packed = _pack("no", False)
        value, single_use = _unpack(packed)
        assert value == "no"
        assert single_use is False

    def test_value_with_colon(self) -> None:
        packed = _pack("a:b", True)
        value, single_use = _unpack(packed)
        assert value == "a:b"
        assert single_use is True

    def test_unrecognized_prefix_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized callback_data prefix"):
            _unpack("nope:something")

    def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized callback_data prefix"):
            _unpack("")


class TestDecodeButtons:
    """Tests for the _decode_buttons validation helper."""

    def test_valid_single_row(self) -> None:
        raw = '[[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]]'
        rows = _decode_buttons(raw)
        assert len(rows) == 1
        assert len(rows[0]) == 2
        assert rows[0][0].label == "Yes"
        assert rows[0][0].value == "yes"
        assert rows[0][1].label == "No"
        assert rows[0][1].value == "no"

    def test_valid_multiple_rows(self) -> None:
        raw = (
            '[[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],'
            '[{"label": "Cancel", "value": "cancel"}]]'
        )
        rows = _decode_buttons(raw)
        assert len(rows) == 2
        assert len(rows[0]) == 2
        assert len(rows[1]) == 1

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON-encoded list"):
            _decode_buttons("not json")

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _decode_buttons("[]")

    def test_empty_row_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _decode_buttons("[[]]")

    def test_non_list_top_level_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _decode_buttons('"hello"')

    def test_non_list_row_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty list"):
            _decode_buttons('["hello"]')

    def test_empty_label_raises(self) -> None:
        raw = '[[{"label": "  ", "value": "yes"}]]'
        with pytest.raises(ValueError, match="empty label"):
            _decode_buttons(raw)

    def test_empty_value_raises(self) -> None:
        raw = '[[{"label": "Yes", "value": ""}]]'
        with pytest.raises(ValueError, match="empty value"):
            _decode_buttons(raw)

    def test_non_string_label_raises(self) -> None:
        raw = '[[{"label": 123, "value": "yes"}]]'
        with pytest.raises(ValueError):
            _decode_buttons(raw)

    def test_non_string_value_raises(self) -> None:
        raw = '[[{"label": "Yes", "value": 123}]]'
        with pytest.raises(ValueError):
            _decode_buttons(raw)

    def test_non_dict_button_raises(self) -> None:
        raw = '[[123]]'
        with pytest.raises(ValueError, match="must be an object"):
            _decode_buttons(raw)

    def test_value_over_byte_limit_raises(self) -> None:
        long_value = "a" * 59
        raw = f'[[{{"label": "OK", "value": "{long_value}"}}]]'
        with pytest.raises(ValueError, match="exceeds 58-byte limit"):
            _decode_buttons(raw)

    def test_value_at_exact_byte_limit_succeeds(self) -> None:
        value = "a" * _MAX_VALUE_BYTES
        raw = f'[[{{"label": "OK", "value": "{value}"}}]]'
        rows = _decode_buttons(raw)
        assert rows[0][0].value == value

    def test_button_count_over_cap_raises(self) -> None:
        row = [{"label": f"B{i}", "value": f"v{i}"} for i in range(_MAX_BUTTON_COUNT + 1)]
        raw = json.dumps([row])
        with pytest.raises(ValueError, match="exceeds cap"):
            _decode_buttons(raw)

    def test_unicode_value_byte_count(self) -> None:
        value = "\U0001f44d" * 14
        assert len(value.encode("utf-8")) == 56
        raw = json.dumps([[{"label": "OK", "value": value}]])
        rows = _decode_buttons(raw)
        assert rows[0][0].value == value


class TestHandlePresentButtons:
    """Tests for the handle_present_buttons extracted handler."""

    async def test_success_returns_message_id(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 4291
        bot.send_message = AsyncMock(return_value=sent_msg)

        result = await handle_present_buttons(
            prompt="Continue?",
            buttons_raw='[[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}]]',
            single_use=True,
            bot=bot,
            chat_id=123,
        )

        assert "is_error" not in result
        assert "message_id: 4291" in result["content"][0]["text"]
        bot.send_message.assert_called_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == 123  # chat_id positional
        assert call_args[0][1] == "Continue?"  # text positional
        markup = call_args.kwargs.get("reply_markup") or call_args[1].get("reply_markup")
        assert markup is not None
        assert markup.inline_keyboard is not None

    async def test_inline_keyboard_layout(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 1
        bot.send_message = AsyncMock(return_value=sent_msg)

        raw = (
            '[[{"label": "Yes", "value": "yes"}, {"label": "No", "value": "no"}],'
            '[{"label": "Cancel", "value": "cancel"}]]'
        )
        result = await handle_present_buttons(
            prompt="Pick one",
            buttons_raw=raw,
            single_use=True,
            bot=bot,
            chat_id=123,
        )

        assert "is_error" not in result
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        kb = markup.inline_keyboard
        assert len(kb) == 2
        assert len(kb[0]) == 2
        assert len(kb[1]) == 1
        assert kb[0][0].text == "Yes"
        assert kb[0][0].callback_data == "btn1:yes"
        assert kb[0][1].text == "No"
        assert kb[0][1].callback_data == "btn1:no"
        assert kb[1][0].text == "Cancel"
        assert kb[1][0].callback_data == "btn1:cancel"

    async def test_multi_use_callback_prefix(self) -> None:
        bot = MagicMock()
        sent_msg = MagicMock()
        sent_msg.message_id = 1
        bot.send_message = AsyncMock(return_value=sent_msg)

        result = await handle_present_buttons(
            prompt="Pick",
            buttons_raw='[[{"label": "A", "value": "a"}]]',
            single_use=False,
            bot=bot,
            chat_id=123,
        )

        assert "is_error" not in result
        markup = bot.send_message.call_args.kwargs["reply_markup"]
        assert markup.inline_keyboard[0][0].callback_data == "btnN:a"

    async def test_decode_failure_returns_error(self) -> None:
        bot = MagicMock()
        bot.send_message = AsyncMock()

        result = await handle_present_buttons(
            prompt="Pick",
            buttons_raw="not json",
            single_use=True,
            bot=bot,
            chat_id=123,
        )

        assert result["is_error"] is True
        bot.send_message.assert_not_called()

    async def test_telegram_api_error_returns_error(self) -> None:
        bot = MagicMock()
        bot.send_message = AsyncMock(
            side_effect=TelegramAPIError(
                method="sendMessage", message="Too many requests"
            ),
        )

        result = await handle_present_buttons(
            prompt="Pick",
            buttons_raw='[[{"label": "A", "value": "a"}]]',
            single_use=True,
            bot=bot,
            chat_id=123,
        )

        assert result["is_error"] is True
        assert "Too many requests" in result["content"][0]["text"]


class TestCreateButtonsServer:
    """Tests for the buttons tool server factory."""

    def test_factory_returns_server(self) -> None:
        bot = MagicMock()

        server = create_buttons_server(bot, 456)

        assert server["name"] == "telegram-buttons"
        assert server["type"] == "sdk"
