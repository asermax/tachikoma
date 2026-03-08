"""Message adapter tests.

Tests for DLT-001: Core agent architecture.
"""

from typing import Any
from unittest.mock import MagicMock

from claude_agent_sdk.types import (
    AssistantMessage,
    AssistantMessageError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)

from tachikoma.adapter import adapt
from tachikoma.events import Error, Result, TextChunk, ToolActivity


def _make_assistant(
    content: list[Any],
    error: AssistantMessageError | None = None,
) -> AssistantMessage:
    return AssistantMessage(content=content, model="claude-sonnet-4-5", error=error)


class TestAdaptAssistantMessage:
    def test_maps_text_block_to_text_chunk(self) -> None:
        msg = _make_assistant([TextBlock(text="Hello!")])

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], TextChunk)
        assert events[0].text == "Hello!"

    def test_maps_tool_use_block_to_tool_activity(self) -> None:
        msg = _make_assistant([
            ToolUseBlock(id="tool-1", name="Read", input={"file_path": "/tmp/f.txt"}),
        ])

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], ToolActivity)
        assert events[0].tool_name == "Read"
        assert events[0].tool_input == {"file_path": "/tmp/f.txt"}
        assert events[0].result == ""

    def test_maps_error_field_to_error_event(self) -> None:
        msg = _make_assistant([], error="server_error")

        events = adapt(msg)

        assert len(events) == 1
        assert isinstance(events[0], Error)
        assert events[0].message == "server_error"

    def test_rate_limit_error_is_recoverable(self) -> None:
        msg = _make_assistant([], error="rate_limit")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_server_error_is_recoverable(self) -> None:
        msg = _make_assistant([], error="server_error")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is True

    def test_auth_error_is_not_recoverable(self) -> None:
        msg = _make_assistant([], error="authentication_failed")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is False

    def test_billing_error_is_not_recoverable(self) -> None:
        msg = _make_assistant([], error="billing_error")

        events = adapt(msg)

        assert isinstance(events[0], Error)
        assert events[0].recoverable is False

    def test_multiple_content_blocks_produce_multiple_events(self) -> None:
        msg = _make_assistant([
            TextBlock(text="Let me check..."),
            ToolUseBlock(id="tool-1", name="Grep", input={"pattern": "TODO"}),
        ])

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
