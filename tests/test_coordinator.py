"""Coordinator integration tests.

Tests for DLT-001: Core agent architecture.
Tests for DLT-027: Session tracking integration.
Mocks ClaudeSDKClient to test the coordinator's end-to-end behavior.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import CLIConnectionError, ProcessError
from claude_agent_sdk.types import (
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from helpers import make_assistant, make_result

from tachikoma.coordinator import Coordinator, _derive_transcript_path
from tachikoma.events import Error, Result, TextChunk, ToolActivity
from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import Session


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
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"

    async def test_yields_tool_activity_for_tool_use(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant(
                [
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "main.py"}),
                ]
            ),
            make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("read main.py")]

        tool_events = [e for e in events if isinstance(e, ToolActivity)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "Read"

    async def test_yields_result_at_stream_end(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="done")]),
            make_result(session_id="sess-42", total_cost_usd=0.03),
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
            make_assistant([TextBlock(text="checking...")]),
            UserMessage(content="tool result"),
            SystemMessage(subtype="init", data={}),
            make_assistant([TextBlock(text="found it")]),
            make_result(),
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

    async def test_forwards_cwd_to_sdk_options(self, mock_sdk) -> None:
        """AC (R8, DLT-023): Coordinator passes cwd to ClaudeAgentOptions."""
        _, mock_cls = mock_sdk

        async with Coordinator(cwd=Path("/workspace")):
            pass

        options = mock_cls.call_args[0][0]
        assert options.cwd == Path("/workspace")


class TestCoordinatorErrorHandling:
    async def test_connection_drop_yields_recoverable_error(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async def _failing_messages():
            yield make_assistant([TextBlock(text="partial")])
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
            yield make_assistant([TextBlock(text="recovered")])
            yield make_result()

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


def _make_mock_registry(active_session=None):
    """Create a mock SessionRegistry with sensible defaults."""
    registry = MagicMock()
    registry.get_active_session = AsyncMock(return_value=active_session)
    registry.create_session = AsyncMock(
        return_value=Session(id="new-session", started_at=datetime.now(UTC))
    )
    registry.close_session = AsyncMock()
    registry.update_metadata = AsyncMock()
    return registry


class TestCoordinatorSessionTracking:
    """Tests for DLT-027: session tracking integration in the coordinator."""

    async def test_first_message_creates_session(self, mock_sdk) -> None:
        """AC: first message with no active session triggers create_session."""
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = [e async for e in coord.send_message("hello")]

        registry.create_session.assert_awaited_once()

    async def test_second_message_reuses_active_session(self, mock_sdk) -> None:
        """AC: subsequent messages with an active session do not create another."""
        client, _ = mock_sdk
        active = Session(id="existing", started_at=datetime.now(UTC))

        # First call: no active session → create; second call: active session exists
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]

        client.receive_messages.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(registry=registry) as coord:
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

        assert registry.create_session.await_count == 1

    async def test_result_event_triggers_metadata_update(self, mock_sdk) -> None:
        """AC: Result event with session_id triggers update_metadata."""
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="done")]),
            make_result(session_id="sdk-session-xyz"),
        )

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = [e async for e in coord.send_message("hello")]

        registry.update_metadata.assert_awaited_once()
        call_kwargs = registry.update_metadata.call_args[1]
        assert call_kwargs["sdk_session_id"] == "sdk-session-xyz"
        assert "transcript_path" in call_kwargs

    async def test_clean_shutdown_closes_active_session(self, mock_sdk) -> None:
        """AC: __aexit__ closes the active session via registry."""
        client, _ = mock_sdk
        active = Session(id="s1", started_at=datetime.now(UTC))
        registry = _make_mock_registry(active_session=active)

        async with Coordinator(registry=registry):
            pass

        registry.close_session.assert_awaited_once_with("s1")

    async def test_works_without_registry(self, mock_sdk) -> None:
        """AC: coordinator is fully functional when no registry is provided."""
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="hello")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1

    async def test_session_tracking_error_does_not_crash_conversation(self, mock_sdk) -> None:
        """AC: registry errors are swallowed — conversation continues normally."""
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="still works")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        registry.create_session.side_effect = SessionRepositoryError("DB down")

        async with Coordinator(registry=registry) as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "still works"


class TestTranscriptPathDerivation:
    """Tests for _derive_transcript_path helper.

    See: DLT-027 design — Known SDK coupling note.
    """

    def test_basic_path_derivation(self) -> None:
        """Basic case: absolute cwd is sanitized and combined with session ID."""
        result = _derive_transcript_path("abc123", Path("/home/user/myproject"))

        home = str(Path.home())
        assert result == f"{home}/.claude/projects/home-user-myproject/abc123.jsonl"

    def test_leading_dash_stripped(self) -> None:
        """Leading '-' from the sanitized cwd is stripped."""
        result = _derive_transcript_path("sess-1", Path("/workspace"))

        # "/workspace" -> "-workspace" -> "workspace" (leading dash stripped)
        home = str(Path.home())
        assert result == f"{home}/.claude/projects/workspace/sess-1.jsonl"

    def test_none_cwd_uses_current_working_directory(self) -> None:
        """When cwd is None, falls back to Path.cwd()."""
        result = _derive_transcript_path("sess-2", None)

        # Should not raise and should end with the session ID
        assert result.endswith("/sess-2.jsonl")

    def test_deep_nested_path(self) -> None:
        """Deeply nested paths are sanitized with dashes."""
        result = _derive_transcript_path("deep-sess", Path("/a/b/c/d"))

        home = str(Path.home())
        assert result == f"{home}/.claude/projects/a-b-c-d/deep-sess.jsonl"


class TestCoordinatorSystemPrompt:
    """Tests for DLT-005: system prompt integration in the coordinator."""

    async def test_system_prompt_provided_sets_sdk_system_prompt(
        self, mock_sdk
    ) -> None:
        """AC: Given system_prompt is provided → system_prompt is a SystemPromptPreset."""
        _, mock_cls = mock_sdk

        async with Coordinator(system_prompt="Custom prompt"):
            pass

        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        assert options.system_prompt["type"] == "preset"
        assert options.system_prompt["preset"] == "claude_code"
        assert options.system_prompt["append"] == "Custom prompt"

    async def test_system_prompt_none_leaves_unset(
        self, mock_sdk
    ) -> None:
        """AC: Given system_prompt is None → ClaudeAgentOptions.system_prompt is None."""
        _, mock_cls = mock_sdk

        async with Coordinator():
            pass
        options = mock_cls.call_args[0][0]
        assert options.system_prompt is None

    async def test_system_prompt_does_not_break_send_message(self, mock_sdk) -> None:
        """AC: Given system_prompt is provided → existing coordinator behavior still works."""
        client, _ = mock_sdk
        client.receive_messages.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )
        async with Coordinator(system_prompt="Custom prompt") as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"
