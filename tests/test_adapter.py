"""Message adapter tests.

Tests for DLT-001: Core agent architecture.
"""

from unittest.mock import MagicMock

from claude_agent_sdk.types import (
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from helpers import make_assistant

from tachikoma.adapter import adapt, sanitize_text
from tachikoma.events import Error, Result, TextChunk, ToolActivity


class TestAdaptAssistantMessage:
    def test_maps_text_block_to_text_chunk(self) -> None:
        msg = make_assistant([TextBlock(text="Hello!")])

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], TextChunk)
        assert events[0].text == "Hello!"

    def test_maps_tool_use_block_to_tool_activity(self) -> None:
        msg = make_assistant(
            [
                ToolUseBlock(id="tool-1", name="Read", input={"file_path": "/tmp/f.txt"}),
            ]
        )

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], ToolActivity)
        assert events[0].tool_name == "Read"
        assert events[0].tool_input == {"file_path": "/tmp/f.txt"}
        assert events[0].result == ""

    def test_maps_error_field_to_error_event(self) -> None:
        msg = make_assistant([], error="server_error")

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Error)
        assert events[0].message == "server_error"

    def test_rate_limit_error_is_recoverable(self) -> None:
        msg = make_assistant([], error="rate_limit")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_server_error_is_recoverable(self) -> None:
        msg = make_assistant([], error="server_error")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_auth_error_is_not_recoverable(self) -> None:
        msg = make_assistant([], error="authentication_failed")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is False

    def test_billing_error_is_not_recoverable(self) -> None:
        msg = make_assistant([], error="billing_error")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is False

    def test_invalid_request_error_is_recoverable(self) -> None:
        msg = make_assistant([], error="invalid_request")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_unknown_error_is_recoverable(self) -> None:
        msg = make_assistant([], error="unknown")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_multiple_content_blocks_produce_multiple_events(self) -> None:
        msg = make_assistant(
            [
                TextBlock(text="Let me check..."),
                ToolUseBlock(id="tool-1", name="Grep", input={"pattern": "TODO"}),
            ]
        )

        events = adapt(msg)

        assert len(events) == 2
        assert isinstance(events[0], TextChunk)
        assert isinstance(events[1], ToolActivity)


class TestAdaptResultMessage:
    def test_maps_success_to_result_with_metadata(self) -> None:
        msg = ResultMessage(
            subtype="success",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=False,
            num_turns=1,
            session_id="sess-abc",
            total_cost_usd=0.05,
            usage={"input_tokens": 100},
        )

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Result)
        assert events[0].session_id == "sess-abc"
        assert events[0].total_cost_usd == 0.05
        assert events[0].usage == {"input_tokens": 100}

    def test_maps_error_result_to_error_event(self) -> None:
        msg = ResultMessage(
            subtype="error",
            duration_ms=500,
            duration_api_ms=400,
            is_error=True,
            num_turns=0,
            session_id="sess-abc",
            result="Budget exceeded",
        )

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Error)
        assert events[0].message == "Budget exceeded"
        assert events[0].recoverable is False


class TestAdaptFilteredMessages:
    def test_user_message_returns_empty(self) -> None:
        msg = UserMessage(content="tool result text")

        assert adapt(msg) == []

    def test_system_message_returns_empty(self) -> None:
        msg = SystemMessage(subtype="init", data={})

        assert adapt(msg) == []

    def test_unknown_message_type_returns_empty(self) -> None:
        msg = MagicMock()

        assert adapt(msg) == []


class TestSanitizeText:
    def test_removes_unpaired_high_surrogate(self) -> None:
        assert sanitize_text("Hello \ud83e World") == "Hello  World"

    def test_removes_unpaired_low_surrogate(self) -> None:
        assert sanitize_text("Hello \udddd World") == "Hello  World"

    def test_preserves_valid_emoji(self) -> None:
        emoji_text = "Hello 🌍🎉🤖 World"
        assert sanitize_text(emoji_text) == emoji_text

    def test_all_surrogates_becomes_empty(self) -> None:
        assert sanitize_text("\ud83e\udddd\ud800\udc00") == ""

    def test_mixed_valid_and_invalid(self) -> None:
        assert sanitize_text("abc\ud83edef") == "abcdef"

    def test_empty_string_passes_through(self) -> None:
        assert sanitize_text("") == ""

    def test_normal_ascii_unchanged(self) -> None:
        text = "The quick brown fox jumps over the lazy dog."
        assert sanitize_text(text) == text

    def test_preserves_multibyte_unicode(self) -> None:
        text = "日本語テスト 中文测试 한국어"
        assert sanitize_text(text) == text



class TestAdaptWithSanitization:
    def test_surrogates_stripped_in_text_chunk(self) -> None:
        msg = make_assistant([TextBlock(text="Clean\ud83eText")])

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], TextChunk)
        assert events[0].text == "CleanText"

    def test_clean_text_unchanged_through_adapt(self) -> None:
        msg = make_assistant([TextBlock(text="Hello 🌍!")])

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], TextChunk)
        assert events[0].text == "Hello 🌍!"

    def test_surrogates_stripped_in_error_message(self) -> None:
        msg = make_assistant([], error="fail\ud83eerror")

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Error)
        assert events[0].message == "failerror"

    def test_surrogates_stripped_in_error_result_message(self) -> None:
        msg = ResultMessage(
            subtype="error",
            duration_ms=500,
            duration_api_ms=400,
            is_error=True,
            num_turns=0,
            session_id="sess-abc",
            result="Bad\ud83eResult",
        )

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Error)
        assert events[0].message == "BadResult"
