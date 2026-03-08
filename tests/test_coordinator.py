"""Coordinator integration tests.

Tests for DLT-001: Core agent architecture.
Mocks ClaudeSDKClient to test the coordinator's end-to-end behavior.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import CLIConnectionError, ProcessError
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)

from tachikoma.coordinator import Coordinator
from tachikoma.events import Error, Result, TextChunk, ToolActivity


def _make_result(
    session_id: str = "sess-test",
    total_cost_usd: float | None = 0.01,
    is_error: bool = False,
    result: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype="success" if not is_error else "error",
        duration_ms=100,
        duration_api_ms=80,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        usage={"input_tokens": 10},
        result=result,
    )


def _make_assistant(content: list) -> AssistantMessage:
    return AssistantMessage(content=content, model="claude-sonnet-4-5")


async def _mock_messages(*messages):
    for msg in messages:
        yield msg


@pytest.fixture
def mock_sdk(mocker):
    """Mock the ClaudeSDKClient class."""
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()
    mock_client.receive_messages = MagicMock()

    mock_cls = mocker.patch("tachikoma.coordinator.ClaudeSDKClient", return_value=mock_client)
    return mock_client, mock_cls


class TestCoordinatorLifecycle:
    async def test_connects_on_enter(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async with Coordinator():
            client.connect.assert_awaited_once()

    async def test_disconnects_on_exit(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async with Coordinator():
            pass

        client.disconnect.assert_awaited_once()

    async def test_connect_failure_propagates(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.connect.side_effect = CLIConnectionError("no CLI")

        with pytest.raises(CLIConnectionError, match="no CLI"):
            async with Coordinator():
                pass


class TestCoordinatorSendMessage:
    async def test_yields_text_chunk_for_text_response(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            _make_assistant([TextBlock(text="Hello!")]),
            _make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"

    async def test_yields_tool_activity_for_tool_use(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            _make_assistant([
                ToolUseBlock(id="t1", name="Read", input={"file_path": "main.py"}),
            ]),
            _make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("read main.py")]

        tool_events = [e for e in events if isinstance(e, ToolActivity)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "Read"

    async def test_yields_result_at_stream_end(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            _make_assistant([TextBlock(text="done")]),
            _make_result(session_id="sess-42", total_cost_usd=0.03),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("do it")]

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1
        assert result_events[0].session_id == "sess-42"
        assert result_events[0].total_cost_usd == 0.03

    async def test_filters_user_and_system_messages(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            _make_assistant([TextBlock(text="checking...")]),
            UserMessage(content="tool result"),
            SystemMessage(subtype="init", data={}),
            _make_assistant([TextBlock(text="found it")]),
            _make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("search")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 2
        assert text_events[0].text == "checking..."
        assert text_events[1].text == "found it"

    async def test_passes_allowed_tools_to_sdk(self, mock_sdk) -> None:
        _, mock_cls = mock_sdk

        async with Coordinator(allowed_tools=["Read", "Glob"]):
            pass

        options = mock_cls.call_args[0][0]
        assert options.allowed_tools == ["Read", "Glob"]


class TestCoordinatorErrorHandling:
    async def test_connection_drop_yields_recoverable_error(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async def _failing_messages():
            yield _make_assistant([TextBlock(text="partial")])
            raise CLIConnectionError("connection lost")

        client.receive_messages.return_value = _failing_messages()

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hello")]

        assert isinstance(events[-1], Error)
        assert events[-1].recoverable is True
        assert "connection lost" in events[-1].message

    async def test_process_error_yields_recoverable_error(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async def _crashing_messages():
            raise ProcessError("CLI crashed", exit_code=1, stderr="segfault")
            yield  # make it an async generator

        client.receive_messages.return_value = _crashing_messages()

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hello")]

        assert isinstance(events[-1], Error)
        assert events[-1].recoverable is True

    async def test_conversation_usable_after_transient_error(self, mock_sdk) -> None:
        """After a transient error, the coordinator should still accept new messages."""
        client, _ = mock_sdk

        async def _failing():
            raise CLIConnectionError("transient")
            yield  # make it an async generator

        async def _ok():
            yield _make_assistant([TextBlock(text="recovered")])
            yield _make_result()

        client.receive_messages.side_effect = [_failing(), _ok()]

        async with Coordinator() as coord:
            events1 = [e async for e in coord.send_message("first")]
            events2 = [e async for e in coord.send_message("second")]

        assert isinstance(events1[-1], Error)

        text_events = [e for e in events2 if isinstance(e, TextChunk)]
        assert text_events[0].text == "recovered"


class TestCoordinatorInterrupt:
    async def test_delegates_to_client_interrupt(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async with Coordinator() as coord:
            await coord.interrupt()

        client.interrupt.assert_awaited_once()
