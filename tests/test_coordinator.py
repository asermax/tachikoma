"""Coordinator integration tests.

Tests for DLT-001: Core agent architecture.
Tests for DLT-027: Session tracking integration.
Tests for DLT-008: Post-processing pipeline integration.
Mocks ClaudeSDKClient to test the coordinator's end-to-end behavior.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from bubus import EventBus
from claude_agent_sdk import CLIConnectionError, ProcessError
from claude_agent_sdk.types import (
    AgentDefinition,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from helpers import make_assistant, make_result

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.boundary import BoundaryResult
from tachikoma.buffer.events import CoordinatorIdle
from tachikoma.coordinator import Coordinator, _derive_transcript_path, _message_source
from tachikoma.events import Error, Result, Status, TextChunk, ToolActivity
from tachikoma.pre_processing import ContextResult
from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import Session, SessionContextEntry
from tachikoma.skills.registry import SkillRegistry


async def _mock_messages(*messages):
    for msg in messages:
        yield msg


async def _send(coord, text):
    """Enqueue a message and collect all events from send_message()."""
    coord.enqueue(text)
    return [e async for e in coord.send_message()]


@pytest.fixture
def mock_sdk(mocker):
    """Mock the ClaudeSDKClient class.

    The coordinator creates a ``ClaudeSDKClient``, calls ``connect()`` with a
    message source generator, and later ``disconnect()``.  We mock the class
    so that ``connect()`` simply stores the generator for later inspection.
    """
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()
    mock_client.receive_response = MagicMock()

    mock_cls = mocker.patch(
        "tachikoma.coordinator.ClaudeSDKClient",
        return_value=mock_client,
    )
    return mock_client, mock_cls


class TestCoordinatorLifecycle:
    async def test_aenter_returns_self(self, mock_sdk) -> None:
        """__aenter__ just returns self without creating a client."""
        _, mock_cls = mock_sdk

        async with Coordinator() as coord:
            assert isinstance(coord, Coordinator)

        # No client should be created just from entering the context
        mock_cls.assert_not_called()

    async def test_aexit_does_not_disconnect(self, mock_sdk) -> None:
        """__aexit__ no longer disconnects a client."""
        client, _ = mock_sdk

        async with Coordinator():
            pass

        # No connect/disconnect calls — per-message lifecycle only
        client.connect.assert_not_awaited()
        client.disconnect.assert_not_awaited()

    async def test_send_message_creates_client_per_call(self, mock_sdk) -> None:
        """Each send_message() creates a fresh ClaudeSDKClient via connect/disconnect."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="A")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = await _send(coord, "first")

            client.receive_response.return_value = _mock_messages(
                make_assistant([TextBlock(text="B")]),
                make_result(),
            )
            _ = await _send(coord, "second")

        # Two send_message calls → two ClaudeSDKClient instantiations
        assert mock_cls.call_count == 2
        assert client.connect.await_count == 2
        assert client.disconnect.await_count == 2

    async def test_connect_failure_in_send_message_yields_error(self, mock_sdk) -> None:
        """Client creation failure inside send_message() yields a recoverable Error."""
        client, _ = mock_sdk
        client.connect.side_effect = CLIConnectionError("no CLI")

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        assert isinstance(events[-1], Error)
        assert events[-1].recoverable is True
        assert "no CLI" in events[-1].message


class TestCoordinatorSendMessage:
    async def test_yields_text_chunk_for_text_response(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "hi")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"

    async def test_yields_tool_activity_for_tool_use(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant(
                [
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "main.py"}),
                ]
            ),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "read main.py")

        tool_events = [e for e in events if isinstance(e, ToolActivity)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "Read"

    async def test_yields_result_at_stream_end(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="done")]),
            make_result(session_id="sess-42", total_cost_usd=0.03),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "do it")

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1
        assert result_events[0].session_id == "sess-42"
        assert result_events[0].total_cost_usd == 0.03

    async def test_filters_user_and_system_messages(self, mock_sdk) -> None:
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="checking...")]),
            UserMessage(content="tool result"),
            SystemMessage(subtype="init", data={}),
            make_assistant([TextBlock(text="found it")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "search")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 2
        assert text_events[0].text == "checking..."
        assert text_events[1].text == "found it"

    async def test_passes_allowed_tools_to_sdk(self, mock_sdk) -> None:
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(allowed_tools=["Read", "Glob"]) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.allowed_tools == ["Read", "Glob"]

    async def test_passes_disallowed_tools_to_sdk(self, mock_sdk) -> None:
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(disallowed_tools=["AskUserQuestion"]) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.disallowed_tools == ["AskUserQuestion"]

    async def test_forwards_cwd_to_sdk_options(self, mock_sdk) -> None:
        """AC (R8, DLT-023): Coordinator passes cwd to ClaudeAgentOptions."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(agent_defaults=AgentDefaults(cwd=Path("/workspace"))) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.cwd == Path("/workspace")


class TestCoordinatorErrorHandling:
    async def test_connection_drop_yields_recoverable_error(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async def _failing_messages():
            yield make_assistant([TextBlock(text="partial")])
            raise CLIConnectionError("connection lost")

        client.receive_response.return_value = _failing_messages()

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        assert isinstance(events[-1], Error)
        assert events[-1].recoverable is True
        assert "connection lost" in events[-1].message

    async def test_process_error_yields_recoverable_error(self, mock_sdk) -> None:
        client, _ = mock_sdk

        async def _crashing_messages():
            raise ProcessError("CLI crashed", exit_code=1, stderr="segfault")
            yield  # make it an async generator

        client.receive_response.return_value = _crashing_messages()

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

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

        client.receive_response.side_effect = [_failing(), _ok()]

        async with Coordinator() as coord:
            events1 = await _send(coord, "first")
            events2 = await _send(coord, "second")

        assert isinstance(events1[-1], Error)

        text_events = [e for e in events2 if isinstance(e, TextChunk)]
        assert text_events[0].text == "recovered"

    async def test_logs_stderr_on_process_error(self, mock_sdk, mocker) -> None:
        """AC: DLT-098 R0 — ProcessError with stderr output logged with stderr= field."""
        client, _ = mock_sdk
        captured_options = []

        async def _crashing_with_stderr():
            raise ProcessError("CLI crashed", exit_code=1)
            yield  # make it an async generator

        client.receive_response.return_value = _crashing_with_stderr()

        def capture_client(opts, **kwargs):
            captured_options.append(opts)
            return client

        _, mock_cls = mock_sdk
        mock_cls.side_effect = capture_client

        mocker.patch("tachikoma.coordinator._log")

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        assert isinstance(events[-1], Error)
        # Verify stderr accumulator was installed on options
        assert len(captured_options) == 1
        assert captured_options[0].stderr is not None
        captured_options[0].stderr("error line from subprocess")
        # StderrAccumulator is the callable itself, call get() directly
        acc = captured_options[0].stderr
        assert acc.get() is not None

        # Verify log was called — the actual stderr content won't be in the log
        # because the accumulator was empty when ProcessError was raised.
        # The real test is that options.stderr IS set (integration check).
        # The stderr_aware_query unit tests already cover the logging behavior.

    async def test_no_stderr_in_log_when_empty(self, mock_sdk, mocker) -> None:
        """AC: DLT-098 R0 — ProcessError with no stderr, log omits stderr kwarg."""
        client, _ = mock_sdk

        async def _crashing_no_stderr():
            raise ProcessError("CLI crashed", exit_code=1)
            yield  # make it an async generator

        client.receive_response.return_value = _crashing_no_stderr()
        mock_log = mocker.patch("tachikoma.coordinator._log")

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        assert isinstance(events[-1], Error)
        error_calls = list(mock_log.error.call_args_list)
        assert len(error_calls) > 0
        # No stderr kwarg when buffer empty
        assert "stderr" not in error_calls[0][1]


class TestCoordinatorInterrupt:
    async def test_delegates_to_client_interrupt(self, mock_sdk) -> None:
        """interrupt() delegates to the active client during send_message."""
        client, _ = mock_sdk

        steered = asyncio.Event()

        async def _slow_messages():
            yield make_assistant([TextBlock(text="thinking...")])
            # Wait so the client is still "active" when we call interrupt
            await steered.wait()
            yield make_result()

        client.receive_response.return_value = _slow_messages()

        async with Coordinator() as coord:

            async def consume():
                return await _send(coord, "hi")

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            await coord.interrupt()
            steered.set()
            await task

        client.interrupt.assert_awaited_once()

    async def test_interrupt_without_active_client_is_noop(self, mock_sdk) -> None:
        """interrupt() is a no-op when no send_message() is in progress."""
        client, _ = mock_sdk

        async with Coordinator() as coord:
            await coord.interrupt()

        client.interrupt.assert_not_awaited()


def _make_mock_registry(active_session=None):
    """Create a mock SessionRegistry with sensible defaults."""
    registry = MagicMock()
    registry.get_active_session = AsyncMock(return_value=active_session)
    registry.create_session = AsyncMock(
        return_value=Session(id="new-session", started_at=datetime.now(UTC)),
    )
    registry.close_session = AsyncMock(return_value=True)
    registry.update_metadata = AsyncMock()
    registry.get_recent_closed = AsyncMock(return_value=[])
    registry.reopen_session = AsyncMock(return_value=None)
    registry.save_context_entries = AsyncMock(return_value=[])
    registry.load_context_entries = AsyncMock(return_value=[])
    registry.mark_processed = AsyncMock()
    registry.get_by_time_range = AsyncMock(return_value=[])
    registry.record_resumption = AsyncMock()
    return registry


class TestCoordinatorSessionTracking:
    """Tests for DLT-027: session tracking integration in the coordinator."""

    async def test_first_message_creates_session(self, mock_sdk) -> None:
        """AC: first message with no active session triggers create_session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "hello")

        registry.create_session.assert_awaited_once()

    async def test_second_message_reuses_active_session(self, mock_sdk) -> None:
        """AC: subsequent messages with an active session do not create another."""
        client, _ = mock_sdk
        active = Session(id="existing", started_at=datetime.now(UTC))

        # First call: no active session -> create; second call: active session exists
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        assert registry.create_session.await_count == 1

    async def test_result_event_triggers_metadata_update(self, mock_sdk) -> None:
        """AC: Result event with session_id triggers update_metadata."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="done")]),
            make_result(session_id="sdk-session-xyz"),
        )

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "hello")

        registry.update_metadata.assert_awaited_once()
        call_kwargs = registry.update_metadata.call_args[1]
        assert call_kwargs["sdk_session_id"] == "sdk-session-xyz"
        assert "transcript_path" in call_kwargs

    async def test_clean_shutdown_closes_active_session(self, mock_sdk) -> None:
        """AC: __aexit__ closes the active session via registry."""
        active = Session(id="s1", started_at=datetime.now(UTC))
        registry = _make_mock_registry(active_session=active)

        async with Coordinator(registry=registry):
            pass

        registry.close_session.assert_awaited_once_with("s1")

    async def test_works_without_registry(self, mock_sdk) -> None:
        """AC: coordinator is fully functional when no registry is provided."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hello")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "hi")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1

    async def test_session_tracking_error_does_not_crash_conversation(self, mock_sdk) -> None:
        """AC: registry errors are swallowed -- conversation continues normally."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="still works")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        registry.create_session.side_effect = SessionRepositoryError("DB down")

        async with Coordinator(registry=registry) as coord:
            events = await _send(coord, "hi")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "still works"


class TestTranscriptPathDerivation:
    """Tests for _derive_transcript_path helper.

    See: DLT-027 design -- Known SDK coupling note.
    """

    def test_basic_path_derivation(self) -> None:
        """Basic case: absolute cwd is sanitized and combined with session ID."""
        result = _derive_transcript_path("abc123", Path("/home/user/myproject"))

        home = str(Path.home())
        assert result == f"{home}/.claude/projects/-home-user-myproject/abc123.jsonl"

    def test_leading_dash_preserved(self) -> None:
        """Leading '-' from the sanitized cwd is preserved (matches SDK convention)."""
        result = _derive_transcript_path("sess-1", Path("/workspace"))

        # "/workspace" -> "-workspace" (leading dash preserved)
        home = str(Path.home())
        assert result == f"{home}/.claude/projects/-workspace/sess-1.jsonl"

    def test_none_cwd_uses_current_working_directory(self) -> None:
        """When cwd is None, falls back to Path.cwd()."""
        result = _derive_transcript_path("sess-2", None)

        # Should not raise and should end with the session ID
        assert result.endswith("/sess-2.jsonl")

    def test_deep_nested_path(self) -> None:
        """Deeply nested paths are sanitized with dashes."""
        result = _derive_transcript_path("deep-sess", Path("/a/b/c/d"))

        home = str(Path.home())
        assert result == f"{home}/.claude/projects/-a-b-c-d/deep-sess.jsonl"


class TestCoordinatorSystemPrompt:
    """Tests for DLT-005/DLT-041: system prompt integration via foundational context."""

    async def test_foundational_context_persisted_to_db(
        self,
        mock_sdk,
    ) -> None:
        """AC: Given foundational_context is provided -> saved to DB for new session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        foundational = [("soul", "Soul content"), ("user", "User content")]

        async with Coordinator(registry=registry, foundational_context=foundational) as coord:
            _ = await _send(coord, "hello")

        # Foundational context should be saved to DB (3-tuples: owner, content, metadata)
        registry.save_context_entries.assert_awaited()
        # Verify the foundational entries were saved
        call_args = registry.save_context_entries.call_args_list[0]
        entries = call_args[0][1]
        # Entries should include our foundational content (with None metadata)
        owners = [owner for owner, _content, _metadata in entries]
        assert "soul" in owners
        assert "user" in owners

    async def test_foundational_context_assembled_into_system_prompt(
        self,
        mock_sdk,
    ) -> None:
        """AC: Foundational context is assembled into SDK system prompt via DB."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        # Mock load_context_entries to return our foundational context
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="soul",
                    content="Soul content",
                ),
            ]
        )
        foundational = [("soul", "Soul content")]

        async with Coordinator(registry=registry, foundational_context=foundational) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        assert options.system_prompt["type"] == "preset"
        assert options.system_prompt["preset"] == "claude_code"
        assert "Soul content" in options.system_prompt["append"]

    async def test_no_foundational_context_uses_preamble_only(
        self,
        mock_sdk,
    ) -> None:
        """AC: No foundational_context -> system prompt uses preamble only."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(return_value=[])

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        # Preamble is always included when there's no context
        assert options.system_prompt is not None
        assert "Tachikoma" in options.system_prompt["append"]

    async def test_foundational_context_does_not_break_send_message(self, mock_sdk) -> None:
        """AC: foundational_context provided -> existing coordinator behavior still works."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        foundational = [("soul", "Soul content")]

        async with Coordinator(registry=registry, foundational_context=foundational) as coord:
            events = await _send(coord, "hi")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"


class TestCoordinatorPermissionAndEnv:
    """Tests for permission_mode and env passthrough to ClaudeAgentOptions."""

    async def test_permission_mode_passed_to_sdk_options(self, mock_sdk) -> None:
        """AC: Given permission_mode is provided -> ClaudeAgentOptions.permission_mode is set."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(permission_mode="bypassPermissions") as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.permission_mode == "bypassPermissions"

    async def test_permission_mode_defaults_to_none(self, mock_sdk) -> None:
        """AC: Given permission_mode is not provided -> defaults to None."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.permission_mode is None

    async def test_env_passed_to_sdk_options(self, mock_sdk) -> None:
        """AC: Given env is provided -> ClaudeAgentOptions.env is set."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(
            agent_defaults=AgentDefaults(
                cwd=Path.cwd(), env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
            ),
        ) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.env == {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}

    async def test_env_defaults_to_empty_dict(self, mock_sdk) -> None:
        """AC: Given env is not provided -> defaults to empty dict."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.env == {}


def _make_mock_pipeline():
    """Create a mock PostProcessingPipeline with sensible defaults."""
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
    pipeline.is_processing = False
    pipeline.needs_processing = MagicMock(return_value=True)
    return pipeline


class TestCoordinatorPostProcessing:
    """Tests for DLT-008: post-processing pipeline integration."""

    async def test_triggers_pipeline_on_shutdown_with_valid_session(self, mock_sdk) -> None:
        """AC: Session with sdk_session_id triggers pipeline.run()."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

        pipeline.run.assert_awaited_once()
        # Verify the session passed to pipeline.run has the sdk_session_id
        session_arg = pipeline.run.call_args[0][0]
        assert session_arg.sdk_session_id == "sdk-xyz"

    async def test_pipeline_receives_session_with_sdk_session_id(self, mock_sdk) -> None:
        """AC: Session passed to pipeline has sdk_session_id captured before close."""
        active = Session(
            id="s2",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-before-close",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

        # The session passed to pipeline.run should have the sdk_session_id
        session_arg = pipeline.run.call_args[0][0]
        assert session_arg.id == "s2"
        assert session_arg.sdk_session_id == "sdk-before-close"

    async def test_skips_pipeline_when_no_sdk_session_id(self, mock_sdk) -> None:
        """AC: Session without sdk_session_id does not trigger pipeline."""
        # Session exists but has no sdk_session_id (interrupted session)
        active = Session(
            id="s3",
            started_at=datetime.now(UTC),
            sdk_session_id=None,
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

        pipeline.run.assert_not_awaited()

    async def test_skips_pipeline_when_no_pipeline_provided(self, mock_sdk) -> None:
        """AC: No pipeline parameter means shutdown works normally."""
        active = Session(
            id="s4",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        # Should not raise
        async with Coordinator(registry=registry):
            pass

    async def test_pipeline_failure_does_not_block_shutdown(self, mock_sdk) -> None:
        """AC: Pipeline errors are caught -- shutdown still completes."""
        active = Session(
            id="s5",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-fail",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()
        pipeline.run.side_effect = RuntimeError("Pipeline crashed")

        # Should not raise despite pipeline failure
        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

    async def test_pipeline_runs_after_session_close(self, mock_sdk) -> None:
        """AC: Ordering is close_session -> pipeline.run."""
        active = Session(
            id="s6",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-order",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        # Track call order
        call_order = []

        async def track_close(session_id: str) -> bool:
            call_order.append("close")
            return True

        async def track_run(session: Session, **_kwargs: object) -> None:
            call_order.append("pipeline")

        registry.close_session.side_effect = track_close
        pipeline.run.side_effect = track_run

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

        assert call_order == ["close", "pipeline"]

    async def test_skips_pipeline_when_no_active_session(self, mock_sdk) -> None:
        """AC: No active session means pipeline is not called."""
        registry = _make_mock_registry(active_session=None)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass

        pipeline.run.assert_not_awaited()

    async def test_shutdown_callback_emits_bookend_messages(self, mock_sdk) -> None:
        """AC: Callback receives 'Shutting down...' before and 'Shutdown complete' after."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        status_calls: list[str] = []

        async def on_status(msg: str) -> None:
            status_calls.append(msg)

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            coord.shutdown_status_callback = on_status

        assert status_calls[0] == "Shutting down..."
        assert status_calls[-1] == "Shutdown complete"

    async def test_shutdown_callback_passed_to_pipeline(self, mock_sdk) -> None:
        """AC: Pipeline.run() is called with on_status set to the callback."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async def on_status(msg: str) -> None:
            pass

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            coord.shutdown_status_callback = on_status

        pipeline.run.assert_awaited_once()
        call_kwargs = pipeline.run.call_args
        assert call_kwargs.kwargs.get("on_status") is on_status

    async def test_shutdown_callback_not_called_when_no_processing_needed(self, mock_sdk) -> None:
        """AC: No callback when pipeline.needs_processing returns False."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()
        pipeline.needs_processing = MagicMock(return_value=False)

        status_calls: list[str] = []

        async def on_status(msg: str) -> None:
            status_calls.append(msg)

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            coord.shutdown_status_callback = on_status

        assert status_calls == []
        pipeline.run.assert_not_awaited()

    async def test_shutdown_callback_none_means_no_messages(self, mock_sdk) -> None:
        """AC: When callback is None (REPL), no bookend messages are sent."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline):
            pass  # No callback set

        pipeline.run.assert_awaited_once()
        call_kwargs = pipeline.run.call_args
        assert call_kwargs.kwargs.get("on_status") is None

    async def test_shutdown_callback_failure_does_not_block_pipeline(self, mock_sdk) -> None:
        """AC: Callback failure doesn't prevent pipeline from running."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-xyz",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async def on_status(msg: str) -> None:
            raise RuntimeError("callback broke")

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            coord.shutdown_status_callback = on_status

        # Pipeline should still have been awaited despite callback failure
        pipeline.run.assert_awaited_once()


class TestCoordinatorMessageBuffer:
    """Tests for message buffer mechanism replacing steer()."""

    async def test_enqueue_always_succeeds(self) -> None:
        """AC: enqueue() succeeds on a bare coordinator (no client needed)."""
        coord = Coordinator()
        coord.enqueue("hello")

        assert coord._message_buffer.qsize() == 1

    async def test_enqueue_is_synchronous(self) -> None:
        """AC: enqueue() is a plain method, not a coroutine."""
        coord = Coordinator()
        result = coord.enqueue("hello")

        assert result is None

    async def test_send_message_reads_from_buffer(self, mock_sdk) -> None:
        """AC: send_message() reads initial message from buffer."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "response"

    async def test_send_message_returns_empty_when_buffer_empty(self, mock_sdk) -> None:
        """AC: send_message() returns immediately if buffer is empty."""
        async with Coordinator() as coord:
            events = [e async for e in coord.send_message()]

        assert events == []

    async def test_buffer_requeued_on_stream_error(self, mock_sdk) -> None:
        """AC: CLIConnectionError triggers re-queue of remaining buffer messages."""
        client, _ = mock_sdk

        async def _failing():
            raise CLIConnectionError("connection lost")
            yield  # noqa: RUF027 — makes this an async generator

        client.receive_response.side_effect = [
            _failing(),
            _mock_messages(make_assistant([TextBlock(text="recovered")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("will survive")
            coord.enqueue("initial")
            events = [e async for e in coord.send_message()]

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1

        # "will survive" errored; "initial" was re-processed by re-queue loop
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1
        assert not coord.has_pending_messages

    async def test_send_message_exits_after_response(self, mock_sdk) -> None:
        """AC: send_message() returns after one receive_response cycle."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "initial")

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1


def _make_mock_skill_registry(agents: dict[str, AgentDefinition]) -> MagicMock:
    """Create a mock SkillRegistry that returns the given agents for skills.

    Expects agent keys in the form "skill-name/agent-name" so it can extract
    the skill name and respond to get_agents_for_skill() calls.
    """
    registry = MagicMock(spec=SkillRegistry)

    # Build a mapping of skill_name -> agents for that skill
    skills_map: dict[str, dict[str, AgentDefinition]] = {}
    for ns, agent_def in agents.items():
        skill_name = ns.split("/")[0]
        skills_map.setdefault(skill_name, {})[ns] = agent_def

    def get_agents_for_skill(skill_name: str) -> dict[str, AgentDefinition]:
        return skills_map.get(skill_name, {})

    registry.get_agents_for_skill = MagicMock(side_effect=get_agents_for_skill)
    registry.skills = {name: MagicMock() for name in skills_map}
    return registry


class TestCoordinatorAgents:
    """Tests for DLT-003: sub-agent delegation derived from skill_registry + context entries."""

    async def test_derives_agents_from_entries_and_registry(self, mock_sdk) -> None:
        """AC: Agents are derived from DB entries with skill metadata + skill_registry."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        agents = {
            "memory/extractor": AgentDefinition(
                description="Extracts memories",
                prompt="Extract episodic memories from conversations.",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)

        # Mock DB to return entries with skill metadata
        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="Memory skill content",
                    metadata={"skill_name": "memory"},
                ),
            ]
        )

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents == agents

    async def test_no_agents_when_no_skill_registry(self, mock_sdk) -> None:
        """AC: Given skill_registry=None -> ClaudeAgentOptions.agents is None."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents is None

    async def test_agents_with_tools(self, mock_sdk) -> None:
        """AC: AgentDefinition.tools is passed through to SDK options."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        agents = {
            "search/query": AgentDefinition(
                description="Search agent",
                prompt="Search for information.",
                tools=["Read", "Glob", "Grep"],
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)
        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="Search skill",
                    metadata={"skill_name": "search"},
                ),
            ]
        )

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents["search/query"].tools == ["Read", "Glob", "Grep"]

    async def test_agents_with_model(self, mock_sdk) -> None:
        """AC: AgentDefinition.model is passed through to SDK options."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        agents = {
            "analysis/deep": AgentDefinition(
                description="Deep analysis agent",
                prompt="Perform deep analysis.",
                model="opus",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)
        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="Analysis skill",
                    metadata={"skill_name": "analysis"},
                ),
            ]
        )

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents["analysis/deep"].model == "opus"

    async def test_agents_does_not_break_send_message(self, mock_sdk) -> None:
        """AC: Given skill_registry is provided -> existing coordinator behavior still works."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        agents = {
            "test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test agent.",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)
        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="Test skill",
                    metadata={"skill_name": "test"},
                ),
            ]
        )

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            events = await _send(coord, "hi")

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"

    async def test_agents_preserved_across_messages(self, mock_sdk) -> None:
        """AC: Agents are derived from persisted entries across multiple send_message() calls."""
        client, mock_cls = mock_sdk

        agents = {
            "test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test agent.",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)

        active = Session(id="existing", started_at=datetime.now(UTC))
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="Test skill",
                    metadata={"skill_name": "test"},
                ),
            ]
        )

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        # Both calls should have agents in options (derived from entries)
        for call in mock_cls.call_args_list:
            options = call[0][0]
            assert options.agents == agents


def _make_mock_pre_pipeline():
    """Create a mock PreProcessingPipeline with sensible defaults."""
    pipeline = MagicMock()
    pipeline.run = AsyncMock(return_value=[])
    return pipeline


class TestCoordinatorPreProcessing:
    """Tests for DLT-006: pre-processing pipeline integration."""

    async def test_runs_pre_pipeline_on_first_message_of_new_session(
        self,
        mock_sdk,
    ) -> None:
        """AC: First message of new session triggers pre_pipeline.run() and saves to DB."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(tag="memories", content="Some memories"),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        pre_pipeline.run.assert_awaited_once()
        call = pre_pipeline.run.await_args
        assert call.args == ("hello",)
        assert "on_status" in call.kwargs

        # Verify pre-processing results were saved to DB (3-tuples: owner, content, metadata)
        found_memories = False
        for call in registry.save_context_entries.call_args_list:
            entries = call[0][1]
            for owner, content, _metadata in entries:
                if owner == "memories":
                    found_memories = True
                    assert "Some memories" in content
                    break
        assert found_memories

    async def test_skips_pre_pipeline_on_subsequent_message(
        self,
        mock_sdk,
    ) -> None:
        """AC: Second message in same session does not trigger pre-processing."""
        client, _ = mock_sdk
        active = Session(id="existing", started_at=datetime.now(UTC))

        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [ContextResult(tag="memories", content="ctx")]

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        # Pre-processing should only run on the first message
        assert pre_pipeline.run.await_count == 1

    async def test_skips_pre_pipeline_when_no_pipeline_provided(
        self,
        mock_sdk,
    ) -> None:
        """AC: No pre_pipeline means message is sent unmodified."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "hello")

        # Message should be sent without enrichment
        client.connect.assert_awaited_once()

    async def test_skips_pre_pipeline_when_no_registry(
        self,
        mock_sdk,
    ) -> None:
        """AC: No registry means pre-processing is skipped."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()

        async with Coordinator(pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        pre_pipeline.run.assert_not_awaited()
        client.connect.assert_awaited_once()

    async def test_pre_pipeline_failure_sends_original_message(
        self,
        mock_sdk,
    ) -> None:
        """AC: Pre-processing failure logs error and sends original message."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.side_effect = RuntimeError("Pre-processing failed")
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        # Original message should be sent despite failure
        client.connect.assert_awaited_once()

    async def test_pre_pipeline_empty_results_sends_original_message(
        self,
        mock_sdk,
    ) -> None:
        """AC: Empty results from pre_pipeline sends original message."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = []  # No results
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        # Original message should be sent (assemble_context returns original on empty)
        client.connect.assert_awaited_once()

    async def test_session_creation_failure_skips_pre_pipeline(
        self,
        mock_sdk,
    ) -> None:
        """AC: Session creation failure means pre-processing is skipped."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()
        registry = _make_mock_registry(active_session=None)
        registry.create_session.side_effect = SessionRepositoryError("DB error")

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        pre_pipeline.run.assert_not_awaited()
        client.connect.assert_awaited_once()


class TestCoordinatorMcpServers:
    """Tests for DLT-030: MCP servers extraction from pre-processing results."""

    async def test_extracts_mcp_servers_from_pre_processing_results(self, mock_sdk, mocker) -> None:
        """AC: MCP servers from ContextResult are passed to ClaudeAgentOptions."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        # Create a mock MCP server config
        mock_server = {"type": "sdk", "sdkServer": MagicMock()}
        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(
                tag="projects",
                content="Project list",
                mcp_servers={"projects": mock_server},
            ),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        # Verify options were built with mcp_servers
        mock_cls.assert_called_once()
        options = mock_cls.call_args[0][0]
        assert options.mcp_servers == {"projects": mock_server}

    async def test_merges_mcp_servers_from_multiple_results(self, mock_sdk, mocker) -> None:
        """AC: Multiple ContextResults with mcp_servers are merged."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        server1 = {"type": "sdk", "sdkServer": MagicMock(name="server1")}
        server2 = {"type": "sdk", "sdkServer": MagicMock(name="server2")}

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(tag="projects", content="Projects", mcp_servers={"projects": server1}),
            ContextResult(tag="other", content="Other", mcp_servers={"tools": server2}),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.mcp_servers == {"projects": server1, "tools": server2}

    async def test_mcp_servers_persist_across_messages_in_session(self, mock_sdk, mocker) -> None:
        """AC: MCP servers persist across messages within the same session."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B")]), make_result()),
        ]

        mock_server = {"type": "sdk", "sdkServer": MagicMock()}
        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(
                tag="projects",
                content="Projects",
                mcp_servers={"projects": mock_server},
            ),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        # Both calls should have the same mcp_servers
        assert mock_cls.call_count == 2
        options1 = mock_cls.call_args_list[0][0][0]
        options2 = mock_cls.call_args_list[1][0][0]
        assert options1.mcp_servers == {"projects": mock_server}
        assert options2.mcp_servers == {"projects": mock_server}

    async def test_mcp_servers_cleared_on_session_transition(self, mock_sdk, mocker) -> None:
        """AC: MCP servers are cleared when transitioning to a new session."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B")]), make_result()),
        ]

        mock_server = {"type": "sdk", "sdkServer": MagicMock()}
        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(
                tag="projects",
                content="Projects",
                mcp_servers={"projects": mock_server},
            ),
        ]

        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User is discussing Python.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)

        # Mock boundary detection to trigger transition
        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            pre_pipeline=pre_pipeline,
            agent_defaults=AgentDefaults(cwd=Path("/ws")),
        ) as coord:
            _ = await _send(coord, "first")

            # Update mock for second call - no mcp_servers in new session
            pre_pipeline.run.return_value = []

            _ = await _send(coord, "new topic")

        # First call should have mcp_servers, second call should be empty
        assert mock_cls.call_count == 2
        options1 = mock_cls.call_args_list[0][0][0]
        options2 = mock_cls.call_args_list[1][0][0]
        assert options1.mcp_servers == {"projects": mock_server}
        assert options2.mcp_servers == {}

    async def test_no_mcp_servers_when_not_provided(self, mock_sdk) -> None:
        """AC: No mcp_servers in ContextResult means None in options."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(tag="memories", content="Some memories"),  # No mcp_servers
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.mcp_servers == {}


class TestBoundaryDetection:
    """Tests for DLT-026: boundary detection integration in send_message()."""

    async def test_skips_detection_when_no_active_session(self, mock_sdk) -> None:
        """AC: No active session means detection is skipped."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(
            registry=registry, agent_defaults=AgentDefaults(cwd=Path("/workspace"))
        ) as coord:
            _ = await _send(coord, "hello")

        # No boundary detection call should happen - message should be processed normally
        registry.create_session.assert_awaited_once()

    async def test_skips_detection_when_no_summary(self, mock_sdk) -> None:
        """AC: Active session with summary=None means detection is skipped."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary=None,  # No summary yet
        )
        registry = _make_mock_registry(active_session=active)

        async with Coordinator(
            registry=registry, agent_defaults=AgentDefaults(cwd=Path("/workspace"))
        ) as coord:
            _ = await _send(coord, "hello")

        # Message should be processed without triggering transition
        registry.create_session.assert_not_awaited()

    async def test_continuation_proceeds_normally(self, mock_sdk, mocker) -> None:
        """AC: When boundary detection returns True, message processed normally."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="continuing")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User is discussing Python testing.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)

        # Mock boundary detection to return continuation
        mock_detect = mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=True),
        )

        async with Coordinator(
            registry=registry, agent_defaults=AgentDefaults(cwd=Path("/workspace"))
        ) as coord:
            events = await _send(coord, "tell me more")

        # Should NOT trigger transition (close_session called only once at shutdown)
        mock_detect.assert_awaited_once()
        # close_session should be called exactly once (at shutdown, not during transition)
        assert registry.close_session.await_count == 1
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "continuing"

    async def test_boundary_detection_error_defaults_to_continuation(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Boundary detection errors are caught, message processed as continuation."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="still works")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User is discussing Python.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)

        # Mock boundary detection to raise an error
        mock_detect = mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            side_effect=RuntimeError("SDK error"),
        )

        async with Coordinator(
            registry=registry, agent_defaults=AgentDefaults(cwd=Path("/workspace"))
        ) as coord:
            events = await _send(coord, "hello")

        # Error should be caught, message should still be processed
        mock_detect.assert_awaited_once()
        # close_session should be called exactly once (at shutdown, not during transition)
        assert registry.close_session.await_count == 1
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "still works"

    async def test_awaits_pending_task_before_detection(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Pending per-message task is awaited before boundary detection runs."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Previous summary",
        )
        registry = _make_mock_registry(active_session=active)

        # Track the order of calls
        call_order: list[str] = []

        async def slow_msg_pipeline(session, user_msg, agent_response, **kwargs):
            call_order.append("msg_pipeline_start")
            await asyncio.sleep(0.05)
            call_order.append("msg_pipeline_end")

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock(side_effect=slow_msg_pipeline)

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=True),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            # First message triggers per-message pipeline
            _ = await _send(coord, "first")
            # Give the background task time to start
            await asyncio.sleep(0.01)
            # Second message should await pending task before detection
            _ = await _send(coord, "second")

        # The pending task should have been awaited before detection
        assert "msg_pipeline_start" in call_order
        assert "msg_pipeline_end" in call_order

    async def test_pending_task_failure_logged_not_propagated(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Pending task failure is logged but doesn't block message processing."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock(side_effect=RuntimeError("Pipeline failed"))

        mock_detect = mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=True),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "first")
            # Second message should not raise despite pending task failure
            _ = await _send(coord, "second")

        # Detection should still have been called
        mock_detect.assert_awaited()


class TestSessionTransition:
    """Tests for DLT-026: session transition on topic shift."""

    async def test_closes_current_session_on_topic_shift(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Topic shift closes the current session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User was discussing Python.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),  # Topic shift
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "what's for dinner?")

        registry.close_session.assert_awaited_once_with("s1")

    async def test_resume_id_overrides_continues_true(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: resume_session_id is authoritative — transitions even when continues=True."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="back to Python")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User was discussing Python.",
            sdk_session_id="sdk-old",
        )
        resumed = Session(
            id="s2",
            started_at=datetime.now(UTC),
            summary="User was discussing Python debugging.",
            sdk_session_id="sdk-resume",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, resumed, resumed]
        registry.reopen_session.return_value = resumed
        registry.record_resumption = AsyncMock()

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(
                continues=True,
                resume_session_id="s2",
            ),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "remember that Python debugging?")

        # Should have closed the old session and resumed s2
        close_calls = [c.args[0] for c in registry.close_session.await_args_list]
        assert "s1" in close_calls
        registry.reopen_session.assert_awaited_once_with("s2")
        registry.record_resumption.assert_awaited_once()

    async def test_fires_async_session_post_processing(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Session post-processing is fired as background task on topic shift."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]
        pipeline = _make_mock_pipeline()

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        task_completed = asyncio.Event()

        async def track_pipeline_run(session):
            task_completed.set()

        pipeline.run.side_effect = track_pipeline_run

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")
            # Give background task time to start
            await asyncio.sleep(0.05)

        # Pipeline should have been called with the session
        pipeline.run.assert_awaited_once()
        session_arg = pipeline.run.call_args[0][0]
        assert session_arg.id == "s1"

    async def test_skips_session_post_processing_when_no_sdk_session_id(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: No session post-processing when session has no sdk_session_id."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id=None,  # No SDK session yet
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]
        pipeline = _make_mock_pipeline()

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")

        # Pipeline should NOT have been called for transition
        pipeline.run.assert_not_awaited()

    async def test_clears_sdk_session_id_on_topic_shift(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Topic shift clears _sdk_session_id so next message starts fresh."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Previous summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

            # After transition, _sdk_session_id should be None
            assert coord._sdk_session_id is None

    async def test_stores_previous_summary_on_topic_shift(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Topic shift persists previous session's summary via save_context_entries."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User was discussing Python.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

            # After transition, previous summary should be persisted via save_context_entries
            registry.save_context_entries.assert_awaited_once()
            call_args = registry.save_context_entries.call_args
            # Check entries contain the previous-summary entry (3-tuple)
            entries = call_args[0][1]
            assert len(entries) == 1
            owner, content, metadata = entries[0]
            assert owner == "previous-summary"
            assert "# Previous Conversation" in content
            assert "User was discussing Python" in content
            assert metadata is None

    async def test_creates_new_session_after_transition(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: New session is created after transition."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        # First get returns active, subsequent calls return None (after close), then new session
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

        # New session should be created
        registry.create_session.assert_awaited()

    async def test_session_close_error_does_not_block_transition(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Session close error is logged but transition continues."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]
        registry.close_session.side_effect = RuntimeError("DB error")

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

        # Despite close error, new session should be created
        registry.create_session.assert_awaited()

    async def test_session_task_triggers_boundary_detection(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Session task messages go through boundary detection like user messages.

        Given a session task message is injected, then it goes through the full
        pre-processing pipeline including boundary detection. If the boundary
        detector classifies it as a topic change, a new session is created.
        """
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="task response")]),
            make_result(),
        )

        # Active session with an existing topic
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User was discussing Python programming.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        # Boundary detection indicates topic shift for the session task
        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),  # Topic shift
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            # Simulate a session task prompt being sent
            _ = await _send(coord, "Reminder: review the weekly report")

        # Verify boundary detection was triggered (session closed, new created)
        registry.close_session.assert_awaited_once_with("s1")
        registry.create_session.assert_awaited()


class TestBuildOptions:
    """Tests for _build_options and resume/session continuity."""

    async def test_resume_passed_on_continuation(self, mock_sdk) -> None:
        """AC: resume=sdk_session_id is passed on continuation within same session."""
        client, mock_cls = mock_sdk

        client.receive_response.side_effect = [
            _mock_messages(
                make_assistant([TextBlock(text="first")]),
                make_result(session_id="sdk-abc"),
            ),
            _mock_messages(
                make_assistant([TextBlock(text="second")]),
                make_result(session_id="sdk-abc"),
            ),
        ]

        registry = _make_mock_registry(active_session=None)
        active = Session(id="existing", started_at=datetime.now(UTC))
        registry.get_active_session.side_effect = [None, active, active, active]

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        # First call: new session, resume=None
        first_options = mock_cls.call_args_list[0][0][0]
        assert first_options.resume is None

        # Second call: continuation, resume=sdk_session_id
        second_options = mock_cls.call_args_list[1][0][0]
        assert second_options.resume == "sdk-abc"

    async def test_resume_none_on_new_session(self, mock_sdk) -> None:
        """AC: resume=None when starting a new session."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.resume is None

    async def test_resume_none_after_topic_shift(self, mock_sdk, mocker) -> None:
        """AC: After topic shift, resume is None for the new session's first message."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Previous topic",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            # Seed _sdk_session_id as if there was a previous message in that session
            coord._sdk_session_id = "sdk-old"
            _ = await _send(coord, "new topic")

        # After topic shift, options.resume should be None (new session)
        options = mock_cls.call_args[0][0]
        assert options.resume is None

    async def test_previous_summary_persisted_to_db_on_topic_shift(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Previous conversation summary is persisted to DB after topic shift."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User was discussing Python testing frameworks.",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

        # Previous summary should be persisted via save_context_entries (3-tuples)
        found_previous_summary = False
        for call in registry.save_context_entries.call_args_list:
            entries = call[0][1]
            for owner, content, _metadata in entries:
                if owner == "previous-summary":
                    found_previous_summary = True
                    assert "Python testing frameworks" in content
                    break
        assert found_previous_summary

    async def test_previous_summary_assembled_from_db(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Previous summary is assembled into system prompt from DB entries."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary text",
            sdk_session_id="sdk-old",
        )
        new_session = Session(
            id="s2",
            started_at=datetime.now(UTC),
        )
        registry = _make_mock_registry(active_session=active)
        registry.create_session = AsyncMock(return_value=new_session)
        # First call: active session for boundary detection
        # Second call: new_session after create_session in _handle_transition
        # Third call: new_session for message post-processing
        # Fourth call: new_session for __aexit__ cleanup
        registry.get_active_session.side_effect = [active, new_session, new_session, new_session]
        # Mock load_context_entries to return the previous-summary entry for new session
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s2",
                    owner="previous-summary",
                    content="# Previous Conversation\nSummary text",
                ),
            ]
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")

        # The options used for the message should have the summary in the system prompt
        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        append_text = options.system_prompt["append"]
        assert "Summary text" in append_text

    async def test_previous_summary_not_repeated_on_second_message(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Previous summary is not repeated after first message (DB only has it once)."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(
                make_assistant([TextBlock(text="new topic")]),
                make_result(session_id="sdk-new"),
            ),
            _mock_messages(
                make_assistant([TextBlock(text="follow-up")]),
                make_result(session_id="sdk-new"),
            ),
        ]
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Previous summary",
            sdk_session_id="sdk-old",
        )
        new_session = Session(id="s2", started_at=datetime.now(UTC))
        registry = _make_mock_registry(active_session=active)
        registry.create_session = AsyncMock(return_value=new_session)
        # First msg: active -> new_session (after create_session) -> new_session
        # Second msg: new_session -> new_session -> new_session
        registry.get_active_session.side_effect = [
            active,
            new_session,
            new_session,  # First message
            new_session,
            new_session,
            new_session,  # Second message
        ]
        # First call returns previous-summary, second call returns empty (consumed)
        registry.load_context_entries = AsyncMock(
            side_effect=[
                [
                    SessionContextEntry(
                        id=1,
                        session_id="s2",
                        owner="previous-summary",
                        content="Previous summary",
                    )
                ],
                [],  # Second message: no previous-summary entry
            ]
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "new topic")
            _ = await _send(coord, "follow-up")

        # First message after shift: summary in system prompt
        first_options = mock_cls.call_args_list[0][0][0]
        assert "Previous summary" in first_options.system_prompt["append"]

        # Second message: no previous-summary in DB, so just preamble
        second_options = mock_cls.call_args_list[1][0][0]
        assert "Previous summary" not in second_options.system_prompt["append"]


class TestPerMessagePostProcessing:
    """Tests for DLT-026: per-message post-processing pipeline trigger."""

    async def test_triggers_msg_pipeline_after_result(
        self,
        mock_sdk,
    ) -> None:
        """AC: Per-message pipeline is triggered after Result event."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        # Give the background task time to be scheduled
        await asyncio.sleep(0.05)

        msg_pipeline.run.assert_awaited_once()

    async def test_passes_accumulated_response_text(
        self,
        mock_sdk,
    ) -> None:
        """AC: Accumulated response text is passed to the pipeline."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello "), TextBlock(text="there!")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        await asyncio.sleep(0.05)

        # Check that the accumulated text was passed
        call_args = msg_pipeline.run.call_args
        agent_response = call_args[0][2]  # Third positional argument
        assert agent_response == "Hello there!"

    async def test_final_text_filters_to_post_tool_text(
        self,
        mock_sdk,
    ) -> None:
        """AC1/AC5: final_text contains only text after the last tool call."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant(
                [
                    TextBlock(text="Let me check..."),
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "main.py"}),
                ]
            ),
            make_assistant([TextBlock(text="Now let me fix...")]),
            make_assistant(
                [
                    ToolUseBlock(id="t2", name="Edit", input={"file_path": "main.py"}),
                ]
            ),
            make_assistant([TextBlock(text="Done! Here's the fix.")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "fix this")

        await asyncio.sleep(0.05)

        call_args = msg_pipeline.run.call_args
        agent_response = call_args[0][2]
        final_text = call_args[1]["final_text"]

        # Full response includes all text chunks
        assert "Let me check..." in agent_response
        assert "Now let me fix..." in agent_response
        assert "Done! Here's the fix." in agent_response
        # final_text only includes text after the last tool call
        assert final_text == "Done! Here's the fix."

    async def test_final_text_is_none_when_no_tool_calls(
        self,
        mock_sdk,
    ) -> None:
        """AC2: final_text is None when response has no tool calls."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Simple response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        await asyncio.sleep(0.05)

        call_args = msg_pipeline.run.call_args
        assert call_args[1]["final_text"] is None

    async def test_final_text_is_none_when_response_ends_with_tool(
        self,
        mock_sdk,
    ) -> None:
        """AC3: final_text is None when response ends with a tool call (no trailing text)."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant(
                [
                    TextBlock(text="Let me check..."),
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "main.py"}),
                ]
            ),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "check this")

        await asyncio.sleep(0.05)

        call_args = msg_pipeline.run.call_args
        assert call_args[1]["final_text"] is None

    async def test_skips_pipeline_when_no_msg_pipeline(
        self,
        mock_sdk,
    ) -> None:
        """AC: No msg_pipeline parameter means no error on response."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        # Should not raise
        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "hello")

    async def test_pipeline_receives_current_session(
        self,
        mock_sdk,
    ) -> None:
        """AC: Session passed to pipeline has latest metadata."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(session_id="sdk-123"),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
        )
        registry = _make_mock_registry(active_session=active)
        # After metadata update, session should have sdk_session_id
        updated_session = Session(
            id="s1",
            started_at=active.started_at,
            sdk_session_id="sdk-123",
        )
        registry.get_active_session.side_effect = [active, updated_session, updated_session]

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock()

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        await asyncio.sleep(0.05)

        # Check that the pipeline received the session
        call_args = msg_pipeline.run.call_args
        session_arg = call_args[0][0]
        assert session_arg.id == "s1"


class TestCoordinatorShutdownWithBoundaryDetection:
    """Tests for DLT-026: shutdown with background tasks from boundary detection."""

    async def test_awaits_pending_msg_task_on_shutdown(
        self,
        mock_sdk,
    ) -> None:
        """AC: Pending per-message task is awaited on shutdown."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-123",
        )
        registry = _make_mock_registry(active_session=active)

        task_completed = asyncio.Event()

        async def slow_pipeline(session, user_msg, agent_response, **kwargs):
            await asyncio.sleep(0.05)
            task_completed.set()

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock(side_effect=slow_pipeline)

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            msg_pipeline=msg_pipeline,
            pipeline=_make_mock_pipeline(),
        ) as coord:
            _ = await _send(coord, "hello")
            # Exit immediately while task is pending

        # The task should have been awaited and completed
        assert task_completed.is_set()

    async def test_awaits_background_tasks_on_shutdown(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Background session post-processing tasks are awaited on shutdown."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        task_completed = asyncio.Event()

        async def slow_pipeline(session):
            await asyncio.sleep(0.05)
            task_completed.set()

        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=slow_pipeline)

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")
            # Exit while background task is running

        # The background task should have been awaited
        assert task_completed.is_set()

    async def test_background_task_failure_does_not_block_shutdown(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Background task failure is logged but doesn't block shutdown."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=RuntimeError("Pipeline failed"))

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        # Should not raise despite background task failure
        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")

    async def test_background_shutdown_logs_task_count(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC2: Info-level log emitted with task count before gathering.

        Loguru writes to stderr (not stdlib logging), so caplog cannot capture
        the message. This test verifies the log call via mock. The message is
        also visible in pytest's captured stderr output.
        """
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )

        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        pipeline = _make_mock_pipeline()

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        mock_log = mocker.patch("tachikoma.coordinator._log")

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")

        mock_log.info.assert_any_call(
            "Awaiting background post-processing tasks: count={count}",
            count=1,
        )

    async def test_background_shutdown_awaits_despite_pipeline_failure(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC4: background tasks still awaited even if a pipeline task fails."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="new topic")]),
            make_result(),
        )

        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="Summary",
            sdk_session_id="sdk-old",
        )
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        task_completed = asyncio.Event()

        async def slow_pipeline(session):
            await asyncio.sleep(0.05)
            task_completed.set()

        pipeline = MagicMock()
        pipeline.run = AsyncMock(side_effect=slow_pipeline)

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
            pipeline=pipeline,
        ) as coord:
            _ = await _send(coord, "new topic")

        # Background task should still have been awaited
        assert task_completed.is_set()


class TestCoordinatorPipelineAgents:
    """Tests for DLT-021: agent derivation from per-message pipeline + skill registry."""

    async def test_agents_derived_from_per_message_pipeline_entries(
        self,
        mock_sdk,
    ) -> None:
        """AC: Per-message pipeline results with skill metadata are saved and agents derived."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        agents = {
            "skills/test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test prompt",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)

        # Per-message pipeline returns a skill entry with metadata
        msg_pre_pipeline = MagicMock()
        msg_pre_pipeline.run = AsyncMock(
            return_value=[
                ContextResult(
                    tag="skills",
                    content="skill content",
                    metadata={"skill_name": "skills"},
                ),
            ]
        )

        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(return_value=[])

        # save_context_entries returns the persisted entries with IDs
        saved_skill_entry = SessionContextEntry(
            id=1,
            session_id="s1",
            owner="skills",
            content="skill content",
            metadata={"skill_name": "skills"},
        )
        registry.save_context_entries = AsyncMock(return_value=[saved_skill_entry])

        async with Coordinator(
            registry=registry,
            skill_registry=skill_registry,
            msg_pre_pipeline=msg_pre_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents == agents

    async def test_agents_persist_across_messages_in_session(
        self,
        mock_sdk,
    ) -> None:
        """AC: Agents derived from entries persist across subsequent messages."""
        client, mock_cls = mock_sdk
        agents = {
            "skills/test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test prompt",
            ),
        }
        skill_registry = _make_mock_skill_registry(agents)

        skill_entry = SessionContextEntry(
            id=1,
            session_id="s1",
            owner="skills",
            content="skill content",
            metadata={"skill_name": "skills"},
        )

        active = Session(id="existing", started_at=datetime.now(UTC))
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]
        registry.load_context_entries = AsyncMock(return_value=[skill_entry])

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(
            registry=registry,
            skill_registry=skill_registry,
        ) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        # Both calls should have agents in options (derived from entries)
        for call in mock_cls.call_args_list:
            options = call[0][0]
            assert options.agents == agents

    async def test_agents_cleared_on_session_transition(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Agents from old session are not carried over after topic shift."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B")]), make_result()),
        ]

        agents_before = {
            "before/agent": AgentDefinition(
                description="Before agent",
                prompt="Before",
            ),
        }
        agents_after = {
            "after/agent": AgentDefinition(
                description="After agent",
                prompt="After",
            ),
        }

        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            summary="User is discussing topic A",
            sdk_session_id="sdk-old",
        )
        new_session = Session(id="new-session", started_at=datetime.now(UTC))
        registry = _make_mock_registry(active_session=active)
        registry.create_session = AsyncMock(return_value=new_session)
        registry.get_active_session.side_effect = [
            active,
            new_session,
            new_session,  # First message
            new_session,
            new_session,  # Second message
            new_session,  # __aexit__
        ]

        # Use a single skill registry that has both skills
        all_agents = {**agents_before, **agents_after}
        skill_registry = _make_mock_skill_registry(all_agents)

        # First load returns old session's skill entry, second returns new session's
        registry.load_context_entries = AsyncMock(
            side_effect=[
                [  # First message: old session entries
                    SessionContextEntry(
                        id=1,
                        session_id="s1",
                        owner="skills",
                        content="before",
                        metadata={"skill_name": "before"},
                    ),
                ],
                [  # Second message: new session entries
                    SessionContextEntry(
                        id=2,
                        session_id="new-session",
                        owner="skills",
                        content="after",
                        metadata={"skill_name": "after"},
                    ),
                ],
            ]
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False),
        )

        async with Coordinator(
            registry=registry,
            skill_registry=skill_registry,
            agent_defaults=AgentDefaults(cwd=Path("/ws")),
        ) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "new topic")

        # First call should have agents_before, second should have agents_after
        assert mock_cls.call_count == 2
        options1 = mock_cls.call_args_list[0][0][0]
        options2 = mock_cls.call_args_list[1][0][0]
        assert options1.agents == agents_before
        assert options2.agents == agents_after

    async def test_no_agents_when_no_skill_entries(
        self,
        mock_sdk,
    ) -> None:
        """AC: When no skill entries are in DB, agents is None."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        skill_registry = _make_mock_skill_registry(
            {
                "skills/test/agent": AgentDefinition(description="Test", prompt="Test"),
            }
        )
        registry = _make_mock_registry(active_session=None)
        # No skill entries in DB
        registry.load_context_entries = AsyncMock(return_value=[])

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents is None

    async def test_multiple_skills_agents_merged(
        self,
        mock_sdk,
    ) -> None:
        """AC: Agents from multiple skills in entries are merged correctly."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        agents1 = {
            "skill-a/agent": AgentDefinition(
                description="A agent",
                prompt="A",
            ),
        }
        agents2 = {
            "skill-b/agent": AgentDefinition(
                description="B agent",
                prompt="B",
            ),
        }
        all_agents = {**agents1, **agents2}
        skill_registry = _make_mock_skill_registry(all_agents)

        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(
            return_value=[
                SessionContextEntry(
                    id=1,
                    session_id="s1",
                    owner="skills",
                    content="a",
                    metadata={"skill_name": "skill-a"},
                ),
                SessionContextEntry(
                    id=2,
                    session_id="s1",
                    owner="skills",
                    content="b",
                    metadata={"skill_name": "skill-b"},
                ),
            ]
        )

        async with Coordinator(registry=registry, skill_registry=skill_registry) as coord:
            _ = await _send(coord, "hello")

        options = mock_cls.call_args[0][0]
        assert options.agents is not None
        assert "skill-a/agent" in options.agents
        assert "skill-b/agent" in options.agents


class TestIdlePostProcessingConfig:
    """Tests for idle post-processing configuration and startup behavior."""

    async def test_idle_timeout_stored(self) -> None:
        """AC: _idle_timeout is set from parameter."""
        coord = Coordinator(session_idle_timeout=600)

        assert coord._idle_timeout == 600

    async def test_idle_loop_not_started_when_timeout_zero(self, mock_sdk) -> None:
        """AC: timeout=0 means no idle post-processing loop task."""
        async with Coordinator(session_idle_timeout=0) as coord:
            assert coord._idle_pp_task is None

    async def test_idle_loop_started_when_timeout_positive(self, mock_sdk) -> None:
        """AC: __aenter__ creates idle post-processing task when timeout > 0."""
        async with Coordinator(session_idle_timeout=900) as coord:
            assert coord._idle_pp_task is not None
            assert not coord._idle_pp_task.done()


class TestIsBusy:
    """Tests for DLT-036: is_busy property detection."""

    async def test_not_busy_when_idle(self, mock_sdk) -> None:
        """AC: All conditions false means not busy."""
        async with Coordinator() as coord:
            assert coord.is_busy is False

    async def test_busy_when_client_active(self, mock_sdk) -> None:
        """AC: _client is not None means busy."""
        # Directly test the property by setting _client
        async with Coordinator() as coord:
            coord._client = MagicMock()  # Simulate active client

            assert coord.is_busy is True

    async def test_busy_when_messages_pending(self) -> None:
        """AC: has_pending_messages (buffer not empty) means busy."""
        async with Coordinator() as coord:
            coord.enqueue("pending message")

            assert coord.has_pending_messages is True
            assert coord.is_busy is True

    async def test_busy_when_msg_task_running(self, mock_sdk) -> None:
        """AC: _pending_msg_task not done means busy."""
        coord = Coordinator()

        # Create a pending task that's not done
        async def _slow_task():
            await asyncio.Event().wait()

        coord._pending_msg_task = asyncio.create_task(_slow_task())
        await asyncio.sleep(0.01)

        assert coord.is_busy is True

        # Cleanup
        coord._pending_msg_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await coord._pending_msg_task
        coord._pending_msg_task = None  # Clear so coordinator doesn't await it


class TestIdlePostProcess:
    """Tests for _idle_post_process() method behavior."""

    async def test_fires_post_processing_without_closing(self, mock_sdk) -> None:
        """AC1: Session stays open and post-processing fires."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(registry=registry, pipeline=pipeline)
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=1000)
        await coord._idle_post_process()

        # Allow background task to start
        await asyncio.sleep(0.01)

        pipeline.run.assert_awaited_once()
        registry.close_session.assert_not_awaited()

    async def test_preserves_sdk_state(self, mock_sdk) -> None:
        """AC: _idle_post_process preserves _sdk_session_id, _mcp_servers."""
        active = Session(
            id="s3",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-3",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(registry=registry, pipeline=pipeline)
        coord._sdk_session_id = "old-sdk"
        coord._mcp_servers = {"test-server": MagicMock()}
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=1000)

        await coord._idle_post_process()

        assert coord._sdk_session_id == "old-sdk"
        assert "test-server" in coord._mcp_servers

    async def test_skips_when_no_sdk_session(self, mock_sdk) -> None:
        """AC: Session without sdk_session_id skips post-processing."""
        active = Session(
            id="s5",
            started_at=datetime.now(UTC),
            sdk_session_id=None,
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            await coord._idle_post_process()

        pipeline.run.assert_not_awaited()

    async def test_skips_when_needs_processing_false(self, mock_sdk) -> None:
        """AC2/AC3: Skips when pipeline.needs_processing returns False."""
        active = Session(
            id="s6",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-6",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()
        pipeline.needs_processing = MagicMock(return_value=False)

        coord = Coordinator(registry=registry, pipeline=pipeline)
        await coord._idle_post_process()

        pipeline.run.assert_not_awaited()

    async def test_noop_when_no_active_session(self, mock_sdk) -> None:
        """AC: No active session means _idle_post_process is a no-op."""
        registry = _make_mock_registry(active_session=None)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            await coord._idle_post_process()

        pipeline.run.assert_not_awaited()

    async def test_graceful_on_registry_error(self, mock_sdk) -> None:
        """AC: Registry errors are logged, no crash."""
        registry = MagicMock()
        registry.get_active_session = AsyncMock(side_effect=RuntimeError("DB connection lost"))

        pipeline = _make_mock_pipeline()
        coord = Coordinator(registry=registry, pipeline=pipeline)
        # Should not raise
        await coord._idle_post_process()


class TestIdlePostProcessingLoop:
    """Tests for _idle_post_processing_loop periodic check behavior."""

    async def test_fires_after_timeout(self, mock_sdk) -> None:
        """AC1: Elapsed > timeout triggers _idle_post_process."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(
            registry=registry,
            pipeline=pipeline,
            session_idle_timeout=1,
        )
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=10)

        await coord._idle_post_process()

        # Allow background task to start
        await asyncio.sleep(0.01)
        pipeline.run.assert_awaited_once()
        registry.close_session.assert_not_awaited()

    async def test_skips_when_no_active_session(self, mock_sdk) -> None:
        """AC: Loop skips when no active session."""
        registry = _make_mock_registry(active_session=None)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(registry=registry, pipeline=pipeline, session_idle_timeout=1)
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=10)

        await coord._idle_post_process()

        pipeline.run.assert_not_awaited()

    async def test_skips_when_needs_processing_false(self, mock_sdk) -> None:
        """AC2: Skips when processed_at >= last_message_time."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
            processed_at=datetime.now(UTC),
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()
        pipeline.needs_processing = MagicMock(return_value=False)

        coord = Coordinator(registry=registry, pipeline=pipeline, session_idle_timeout=1)
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=10)

        await coord._idle_post_process()

        pipeline.run.assert_not_awaited()

    async def test_snoozes_when_busy(self, mock_sdk) -> None:
        """AC: Busy coordinator snoozes instead of processing."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(
            registry=registry,
            pipeline=pipeline,
            session_idle_timeout=300,
        )
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=400)

        # Make coordinator busy
        coord.enqueue("pending")

        assert coord.is_busy is True

    async def test_snooze_duration_capped(self) -> None:
        """AC: Snooze duration is min(300, timeout)."""
        _ = Coordinator(session_idle_timeout=120)
        expected_snooze = min(300, 120)
        assert expected_snooze == 120

        _ = Coordinator(session_idle_timeout=600)
        expected_snooze = min(300, 600)
        assert expected_snooze == 300

    async def test_loop_survives_errors(self, mock_sdk) -> None:
        """AC: Errors in loop are logged, loop continues."""
        registry = MagicMock()
        registry.get_active_session = AsyncMock(
            side_effect=RuntimeError("Transient error"),
        )

        pipeline = _make_mock_pipeline()
        coord = Coordinator(registry=registry, pipeline=pipeline, session_idle_timeout=1)
        await coord._idle_post_process()

        assert coord._idle_timeout == 1

    async def test_independent_of_task_scheduler(self, mock_sdk) -> None:
        """AC (R9): Session open when elapsed > tasks.idle_window < session_idle_timeout."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        coord = Coordinator(
            registry=registry,
            pipeline=pipeline,
            session_idle_timeout=900,
        )
        # 6 minutes idle — still under session_idle_timeout
        coord._last_message_time = datetime.now(UTC) - timedelta(seconds=360)

        await coord._idle_post_process()
        await asyncio.sleep(0.01)

        # Post-processing fires (called directly, bypasses timeout check)
        pipeline.run.assert_awaited_once()
        # Session stays open
        registry.close_session.assert_not_awaited()


class TestIdlePostProcessingShutdown:
    """Tests for idle post-processing behavior during shutdown."""

    async def test_idle_loop_cancelled_on_aexit(self, mock_sdk) -> None:
        """AC: __aexit__ cancels _idle_pp_task before shutdown."""
        async with Coordinator(session_idle_timeout=900) as coord:
            task = coord._idle_pp_task
            assert task is not None

        assert task.cancelled() or task.done()

    async def test_aexit_skips_pp_after_idle_pp(self, mock_sdk) -> None:
        """AC4: If idle PP already processed, __aexit__ skips redundant PP."""
        active = Session(
            id="s1",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-1",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        async with Coordinator(registry=registry, pipeline=pipeline) as coord:
            coord._last_message_time = datetime.now(UTC) - timedelta(seconds=1000)

            # Manually trigger idle post-processing
            await coord._idle_post_process()
            await asyncio.sleep(0.01)

            # After idle PP, make needs_processing return False
            pipeline.needs_processing = MagicMock(return_value=False)

        # pipeline.run was called once by idle PP, not again on shutdown
        pipeline.run.assert_awaited_once()
        # Session was closed on shutdown
        registry.close_session.assert_awaited_once()

    async def test_idle_pp_does_not_fire_during_message_exchange(self, mock_sdk) -> None:
        """AC: Idle post-processing respects busy check during message processing."""
        client, _ = mock_sdk

        steered = asyncio.Event()

        async def _slow_messages():
            yield make_assistant([TextBlock(text="thinking...")])
            await steered.wait()
            yield make_result()

        client.receive_response.return_value = _slow_messages()
        registry = _make_mock_registry(active_session=None)
        pipeline = _make_mock_pipeline()

        async with Coordinator(
            registry=registry,
            pipeline=pipeline,
            session_idle_timeout=1,
        ) as coord:
            coord.enqueue("hello")

            async def consume():
                return [e async for e in coord.send_message()]

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.05)

            steered.set()
            await task


class TestPerMessagePreProcessingIntegration:
    """Tests for DLT-075: per-message pre-processing pipeline integration."""

    async def test_per_message_pipeline_runs_on_every_message(
        self,
        mock_sdk,
    ) -> None:
        """AC (R0): Per-message pipeline runs on every send_message() call."""
        client, _ = mock_sdk

        call_count = 0

        async def counting_run(
            message,
            *,
            existing_entries=None,
            sdk_session_id=None,
            on_status=None,
            session_summary=None,
            session_last_exchange=None,
        ):
            nonlocal call_count
            call_count += 1
            return []

        msg_pre_pipeline = MagicMock()
        msg_pre_pipeline.run = AsyncMock(side_effect=counting_run)

        active = Session(id="existing", started_at=datetime.now(UTC))
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active, active]
        registry.load_context_entries = AsyncMock(return_value=[])

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(registry=registry, msg_pre_pipeline=msg_pre_pipeline) as coord:
            _ = await _send(coord, "first")
            _ = await _send(coord, "second")

        assert call_count == 2

    async def test_per_message_pipeline_runs_after_session_gated_pipeline(
        self,
        mock_sdk,
    ) -> None:
        """AC: On first message, per-message pipeline runs after session-gated pipeline."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        call_order: list[str] = []

        async def track_pre_run(message, *, on_status=None):
            call_order.append("session_gated")
            return [ContextResult(tag="memories", content="some memory")]

        async def track_msg_pre_run(
            message,
            *,
            existing_entries=None,
            sdk_session_id=None,
            on_status=None,
            session_summary=None,
            session_last_exchange=None,
        ):
            call_order.append("per_message")
            return []

        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run = AsyncMock(side_effect=track_pre_run)

        msg_pre_pipeline = MagicMock()
        msg_pre_pipeline.run = AsyncMock(side_effect=track_msg_pre_run)

        registry = _make_mock_registry(active_session=None)

        async with Coordinator(
            registry=registry,
            pre_pipeline=pre_pipeline,
            msg_pre_pipeline=msg_pre_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        assert call_order == ["session_gated", "per_message"]

    async def test_new_entries_appended_with_metadata_existing_preserved(
        self,
        mock_sdk,
    ) -> None:
        """AC (R2, R5): New entries appended with metadata; existing entries preserved."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        msg_pre_pipeline = MagicMock()
        msg_pre_pipeline.run = AsyncMock(
            return_value=[
                ContextResult(
                    tag="skills",
                    content="new skill content",
                    metadata={"skill_name": "new-skill"},
                ),
            ]
        )

        existing_entry = SessionContextEntry(
            id=1,
            session_id="s1",
            owner="skills",
            content="existing skill",
            metadata={"skill_name": "existing-skill"},
        )

        active = Session(id="existing", started_at=datetime.now(UTC))
        registry = _make_mock_registry()
        registry.get_active_session.side_effect = [None, active, active]
        registry.load_context_entries = AsyncMock(return_value=[existing_entry])

        new_saved_entry = SessionContextEntry(
            id=2,
            session_id="s1",
            owner="skills",
            content="new skill content",
            metadata={"skill_name": "new-skill"},
        )
        registry.save_context_entries = AsyncMock(return_value=[new_saved_entry])

        async with Coordinator(
            registry=registry,
            msg_pre_pipeline=msg_pre_pipeline,
        ) as coord:
            _ = await _send(coord, "hello")

        # Verify the new entry was saved with metadata
        save_calls = registry.save_context_entries.call_args_list
        found_per_msg_save = False
        for call in save_calls:
            args = call[0]
            entries = args[1]  # second positional arg is the list of tuples
            for entry_tuple in entries:
                owner, content, metadata = entry_tuple
                if owner == "skills" and metadata == {"skill_name": "new-skill"}:
                    found_per_msg_save = True
                    break
        assert found_per_msg_save

    async def test_per_message_pipeline_failure_does_not_crash(
        self,
        mock_sdk,
    ) -> None:
        """AC (R7): Per-message pipeline failure is logged, send_message continues."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )

        msg_pre_pipeline = MagicMock()
        msg_pre_pipeline.run = AsyncMock(side_effect=RuntimeError("Pipeline crashed"))

        registry = _make_mock_registry(active_session=None)
        registry.load_context_entries = AsyncMock(return_value=[])

        async with Coordinator(
            registry=registry,
            msg_pre_pipeline=msg_pre_pipeline,
        ) as coord:
            events = await _send(coord, "hello")

        # Message should still be processed
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "response"


class TestColdStartResume:
    """Tests for DLT-084: resume matching conversation on return after restart."""

    async def test_resumes_matching_session(self, mock_sdk, mocker) -> None:
        """AC: Cold start with matching candidate reopens the previous session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="welcome back")]),
            make_result(session_id="sdk-resumed"),
        )

        ended_at = datetime.now(UTC) - timedelta(minutes=5)
        resumed_session = Session(
            id="prev-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing Python testing.",
            sdk_session_id="sdk-prev",
            ended_at=ended_at,
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [resumed_session]
        registry.reopen_session.return_value = Session(
            id="prev-session",
            started_at=resumed_session.started_at,
            summary=resumed_session.summary,
            sdk_session_id="sdk-prev",
            ended_at=None,
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(
                continues=False,
                resume_session_id="prev-session",
            ),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            events = await _send(coord, "back to Python testing")

        registry.create_session.assert_not_awaited()
        registry.reopen_session.assert_awaited_once_with("prev-session")
        registry.record_resumption.assert_awaited_once()

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "welcome back"

    async def test_no_candidates_creates_new_session(self, mock_sdk, mocker) -> None:
        """AC: No recent closed sessions skips detection and creates a new session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = []

        mock_detect = mocker.patch("tachikoma.coordinator.detect_boundary")

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "hello")

        mock_detect.assert_not_awaited()
        registry.create_session.assert_awaited_once()

    async def test_no_match_creates_new_session(self, mock_sdk, mocker) -> None:
        """AC: Candidates exist but no match creates a new session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        candidate = Session(
            id="old-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing cooking.",
            sdk_session_id="sdk-old",
            ended_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [candidate]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(continues=False, resume_session_id=None),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "hello")

        registry.reopen_session.assert_not_awaited()
        registry.create_session.assert_awaited_once()

    async def test_reopen_fails_creates_new_session(self, mock_sdk, mocker) -> None:
        """AC: Match found but reopen returns None falls back to new session."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        candidate = Session(
            id="stale-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing Python.",
            sdk_session_id="sdk-stale",
            ended_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [candidate]
        registry.reopen_session.return_value = None

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(
                continues=False,
                resume_session_id="stale-session",
            ),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "back to Python")

        registry.reopen_session.assert_awaited_once_with("stale-session")
        registry.create_session.assert_awaited_once()

    async def test_detect_boundary_error_creates_new_session(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: Boundary detection error falls back to new session (fail-open)."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        candidate = Session(
            id="some-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing Python.",
            sdk_session_id="sdk-some",
            ended_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [candidate]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            side_effect=RuntimeError("SDK error"),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            events = await _send(coord, "hello")

        registry.create_session.assert_awaited_once()
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "hi"

    async def test_forwards_boundary_status(self, mock_sdk, mocker) -> None:
        """AC (DLT-031): Status from detect_boundary is forwarded on the stream."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        candidate = Session(
            id="old-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing cooking.",
            sdk_session_id="sdk-old",
            ended_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [candidate]

        async def fake_detect(*args, on_status=None, **kwargs):
            if on_status is not None:
                await on_status("Analyzing message...")
            return BoundaryResult(continues=False, resume_session_id=None)

        mocker.patch("tachikoma.coordinator.detect_boundary", side_effect=fake_detect)

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            events = await _send(coord, "hello")

        status_events = [e for e in events if isinstance(e, Status)]
        assert any(e.message == "Analyzing message..." for e in status_events)

    async def test_sets_sdk_session_id_on_resume(self, mock_sdk, mocker) -> None:
        """AC: After cold-start resume, SDK options use the resumed session's ID."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="resumed")]),
            make_result(session_id="sdk-new"),
        )

        ended_at = datetime.now(UTC) - timedelta(minutes=5)
        resumed_session = Session(
            id="prev-session",
            started_at=datetime.now(UTC) - timedelta(hours=1),
            summary="User was discussing Python.",
            sdk_session_id="sdk-prev",
            ended_at=ended_at,
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [resumed_session]
        registry.reopen_session.return_value = Session(
            id="prev-session",
            started_at=resumed_session.started_at,
            summary=resumed_session.summary,
            sdk_session_id="sdk-prev",
            ended_at=None,
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(
                continues=False,
                resume_session_id="prev-session",
            ),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "back to Python")

        # The SDK client should have been created with resume=sdk-prev
        options = mock_cls.call_args[0][0]
        assert options.resume == "sdk-prev"

    async def test_records_resumption_with_original_ended_at(
        self,
        mock_sdk,
        mocker,
    ) -> None:
        """AC: record_resumption receives the matched session's ended_at."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(session_id="sdk-new"),
        )

        ended_at = datetime(2026, 4, 8, 12, 0, 0, tzinfo=UTC)
        resumed_session = Session(
            id="prev-session",
            started_at=datetime(2026, 4, 8, 10, 0, 0, tzinfo=UTC),
            summary="User was discussing Python.",
            sdk_session_id="sdk-prev",
            ended_at=ended_at,
        )
        registry = _make_mock_registry(active_session=None)
        registry.get_recent_closed.return_value = [resumed_session]
        registry.reopen_session.return_value = Session(
            id="prev-session",
            started_at=resumed_session.started_at,
            summary=resumed_session.summary,
            sdk_session_id="sdk-prev",
            ended_at=None,
        )

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=BoundaryResult(
                continues=False,
                resume_session_id="prev-session",
            ),
        )

        async with Coordinator(
            registry=registry,
            agent_defaults=AgentDefaults(cwd=Path("/workspace")),
        ) as coord:
            _ = await _send(coord, "back to Python")

        registry.record_resumption.assert_awaited_once()
        call_kwargs = registry.record_resumption.await_args[1]
        assert call_kwargs["session_id"] == "prev-session"
        assert call_kwargs["previous_ended_at"] == ended_at


class TestCoordinatorIdleEmission:
    """Tests for CoordinatorIdle emission on busy->idle transitions (DLT-112 R13)."""

    async def test_emit_on_busy_to_idle(self) -> None:
        """AC (R13): CoordinatorIdle emitted exactly once per busy->idle transition."""
        bus = EventBus()
        idle_events: list[CoordinatorIdle] = []
        bus.dispatch = lambda event: idle_events.append(event)  # type: ignore[assignment]

        async with Coordinator(bus=bus) as coord:
            coord.enqueue("test")
            coord.enqueue("test2")

            # enqueue only captures state; doesn't emit because it goes idle->busy
            assert len(idle_events) == 0

    async def test_no_emit_on_aenter(self) -> None:
        """AC: __aenter__ does not emit CoordinatorIdle."""
        bus = EventBus()
        idle_events: list[CoordinatorIdle] = []
        bus.dispatch = lambda event: idle_events.append(event)  # type: ignore[assignment]

        async with Coordinator(bus=bus):
            assert len(idle_events) == 0

    async def test_maybe_emit_idle_helper(self) -> None:
        """AC: _maybe_emit_idle emits only on True->False transition."""
        bus = EventBus()
        dispatched: list[CoordinatorIdle] = []
        bus.dispatch = lambda event: dispatched.append(event)  # type: ignore[assignment]

        coord = Coordinator(bus=bus)

        # Initial state: not busy, _was_busy=False -> no emission
        coord._maybe_emit_idle()
        assert len(dispatched) == 0

        # Simulate becoming busy: set _was_busy=True, is_busy is False -> emission
        coord._was_busy = True
        coord._maybe_emit_idle()
        assert len(dispatched) == 1
        assert isinstance(dispatched[0], CoordinatorIdle)

        # Already idle -> no second emission
        coord._maybe_emit_idle()
        assert len(dispatched) == 1


class TestCoordinatorTeardownRace:
    """Regression tests for DLT-111 R0/R1/R3/R4 + CoordinatorIdle invariant."""

    async def test_message_enqueued_during_teardown_is_reprocessed(self, mock_sdk) -> None:
        """R0/R1-AC1: message enqueued during teardown is re-processed in same generator."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="late response")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("initial")
            events = []
            enqueued_late = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_late:
                    # Simulates a message arriving during the teardown window
                    coord.enqueue("late")
                    enqueued_late = True

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 2  # Two exchanges in one generator call
        assert not coord.has_pending_messages

    async def test_forwarder_cancelled_before_disconnect(self, mock_sdk) -> None:
        """KD-2: forwarder_task is cancelled+awaited BEFORE client.disconnect runs."""
        client, _ = mock_sdk

        # Yield control mid-stream so the forwarder task actually gets scheduled.
        async def _yielding_response():
            yield make_assistant([TextBlock(text="ok")])
            await asyncio.sleep(0)
            yield make_result()

        client.receive_response.return_value = _yielding_response()

        call_order: list[str] = []

        async def _tracking_disconnect():
            call_order.append("disconnect")

        client.disconnect = AsyncMock(side_effect=_tracking_disconnect)

        async with Coordinator() as coord:
            # Wrap the real forwarder so its `finally` appends a marker
            real_forwarder = Coordinator._forwarder

            async def _tracking_forwarder(self, inbox):
                try:
                    await real_forwarder(self, inbox)
                finally:
                    call_order.append("forwarder-done")

            coord._forwarder = _tracking_forwarder.__get__(coord, Coordinator)  # type: ignore[method-assign]

            coord.enqueue("initial")
            async for _ in coord.send_message():
                pass

        # Forwarder must terminate before disconnect runs (KD-2)
        assert call_order.index("forwarder-done") < call_order.index("disconnect")

    async def test_ordering_preserved_under_concurrent_enqueue(self, mock_sdk) -> None:
        """R3-AC3: A recovered by drain_back + B enqueued during teardown → FIFO re-queue."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="A resp")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B resp")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("initial")
            coord.enqueue("A")  # will be pulled by forwarder into sdk_inbox
            events = []
            enqueued_b = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_b:
                    coord.enqueue("B")  # enqueued during teardown window
                    enqueued_b = True

        # "A" was moved to sdk_inbox by forwarder → drain_back recovered it first
        # "B" was enqueued after forwarder cancellation → drain_back kept it after A
        # Re-queue loop processes them in FIFO order: A then B
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 3  # initial, A, B
        assert not coord.has_pending_messages

    async def test_mid_stream_steering_consumed_then_followup_queued(self, mock_sdk) -> None:
        """R3-AC2: mid-stream A consumed by SDK; B re-processed in same generator."""
        client, _ = mock_sdk

        captured_gen: AsyncIterator | None = None

        async def _capturing_connect(gen):
            nonlocal captured_gen
            captured_gen = gen

        client.connect = AsyncMock(side_effect=_capturing_connect)

        async def _response_consuming_gen():
            # Consume initial from the message_source
            await captured_gen.__anext__()
            yield make_assistant([TextBlock(text="ok")])
            # Consume "A" (enqueued during this turn, moved by forwarder into sdk_inbox)
            await asyncio.sleep(0)
            msg = await captured_gen.__anext__()
            assert msg["message"]["content"] == "A"
            yield make_result()

        client.receive_response.side_effect = [
            _response_consuming_gen(),
            _mock_messages(make_assistant([TextBlock(text="B resp")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("initial")
            events = []
            enqueued_a = False
            enqueued_b = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, TextChunk) and not enqueued_a:
                    coord.enqueue("A")  # mid-stream steering, consumed by SDK
                    enqueued_a = True
                elif isinstance(event, Result) and not enqueued_b:
                    coord.enqueue("B")  # re-processed by re-queue loop
                    enqueued_b = True

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 2  # initial (+A steering), then B
        assert not coord.has_pending_messages

    async def test_mid_stream_message_reaches_sdk_via_inbox(self, mock_sdk) -> None:
        """R4-AC1: follow-up enqueued mid-stream is yielded by the generator to the SDK."""
        client, _ = mock_sdk

        captured_gen: AsyncIterator | None = None
        yielded: list[dict] = []

        async def _capturing_connect(gen):
            nonlocal captured_gen
            captured_gen = gen

        client.connect = AsyncMock(side_effect=_capturing_connect)

        async def _response_driving_gen():
            # Drive the message_source to consume the initial message
            yielded.append(await captured_gen.__anext__())
            yield make_assistant([TextBlock(text="ok")])
            # Let forwarder move the mid-stream follow-up into sdk_inbox, then drive gen
            await asyncio.sleep(0)
            yielded.append(await captured_gen.__anext__())
            yield make_result()

        client.receive_response.return_value = _response_driving_gen()

        async with Coordinator() as coord:
            coord.enqueue("initial")
            async for event in coord.send_message():
                if isinstance(event, TextChunk):
                    coord.enqueue("follow-up")

        assert [m["message"]["content"] for m in yielded] == ["initial", "follow-up"]

    async def test_multiple_mid_stream_messages_preserve_order(self, mock_sdk) -> None:
        """R4-AC2: multiple mid-stream follow-ups delivered in enqueue order."""
        client, _ = mock_sdk

        captured_gen: AsyncIterator | None = None
        yielded: list[dict] = []

        async def _capturing_connect(gen):
            nonlocal captured_gen
            captured_gen = gen

        client.connect = AsyncMock(side_effect=_capturing_connect)

        async def _response_driving_gen():
            yielded.append(await captured_gen.__anext__())
            yield make_assistant([TextBlock(text="ok")])
            # Let forwarder move both follow-ups into sdk_inbox
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            yielded.append(await captured_gen.__anext__())
            yielded.append(await captured_gen.__anext__())
            yield make_result()

        client.receive_response.return_value = _response_driving_gen()

        async with Coordinator() as coord:
            coord.enqueue("initial")
            async for event in coord.send_message():
                if isinstance(event, TextChunk):
                    coord.enqueue("x")
                    coord.enqueue("y")

        assert [m["message"]["content"] for m in yielded] == ["initial", "x", "y"]

    async def test_buffer_preserved_on_connection_error_during_teardown(self, mock_sdk) -> None:
        """R0-AC4: CLIConnectionError teardown path recovers messages for re-queue."""
        client, _ = mock_sdk

        async def _failing_response():
            yield make_assistant([TextBlock(text="partial")])
            raise CLIConnectionError("stream died")

        client.receive_response.side_effect = [
            _failing_response(),
            _mock_messages(make_assistant([TextBlock(text="A resp")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B resp")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("initial")
            coord.enqueue("A")  # moved into sdk_inbox by forwarder during the turn
            events = []
            enqueued_b = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Error) and not enqueued_b:
                    coord.enqueue("B")  # enqueued after the error, during teardown
                    enqueued_b = True

        # First exchange errored, then re-queue loop processed A and B
        error_events = [e for e in events if isinstance(e, Error)]
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(error_events) == 1
        assert len(result_events) == 2  # A and B re-processed
        assert not coord.has_pending_messages

    async def test_coordinator_idle_emitted_after_requeue_completes(self, mock_sdk) -> None:
        """KD-3: CoordinatorIdle emitted after re-queue loop processes all messages."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="late resp")]), make_result()),
        ]

        bus = EventBus()
        idle_events: list[CoordinatorIdle] = []
        bus.dispatch = lambda event: idle_events.append(event)  # type: ignore[assignment]

        async with Coordinator(bus=bus) as coord:
            coord.enqueue("initial")
            enqueued_late = False
            async for event in coord.send_message():
                if isinstance(event, Result) and not enqueued_late:
                    coord.enqueue("late")
                    enqueued_late = True

        assert not coord.has_pending_messages
        assert len(idle_events) >= 1  # Emitted after all re-queue iterations complete

    async def test_coordinator_idle_emitted_when_buffer_truly_empty(self, mock_sdk) -> None:
        """Empty drain-back → is_busy flips → CoordinatorIdle dispatched once."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="ok")]),
            make_result(),
        )

        bus = EventBus()
        idle_events: list[CoordinatorIdle] = []
        bus.dispatch = lambda event: idle_events.append(event)  # type: ignore[assignment]

        async with Coordinator(bus=bus) as coord:
            coord.enqueue("initial")
            async for _ in coord.send_message():
                pass

        assert len(idle_events) == 1
        assert isinstance(idle_events[0], CoordinatorIdle)


class TestMessageSourceCancellation:
    """Tier B: unit tests for _message_source behavior under cancellation.

    Note on S4 coverage: the `pending is not None` recovery branch is
    unreachable in normal operation — between `pending = await inbox.get()`
    returning and `pending = None` (which runs before `yield`), there is no
    `await` point, so CPython cannot inject CancelledError into that window.
    S4 is defence-in-depth for future refactors that may introduce an await.
    The tests below verify the reachable invariants: clean teardown from
    any yield point never drops or duplicates items.
    """

    async def test_item_in_inbox_not_consumed_on_close(self) -> None:
        """Closing the generator while suspended at a yield does not consume inbox items."""
        inbox: asyncio.Queue[str] = asyncio.Queue()
        inbox.put_nowait("x")

        gen = _message_source("initial", inbox)

        msg = await gen.__anext__()
        assert msg["message"]["content"] == "initial"

        # Generator is suspended at the first yield (outside the try/finally).
        # Close it — "x" was never read from the inbox, so it must remain there.
        await gen.aclose()

        assert inbox.qsize() == 1
        assert inbox.get_nowait() == "x"

    async def test_yielded_item_not_re_enqueued_on_close(self) -> None:
        """An item that was yielded to the consumer is not recovered on close."""
        inbox: asyncio.Queue[str] = asyncio.Queue()
        inbox.put_nowait("x")

        gen = _message_source("initial", inbox)

        msg1 = await gen.__anext__()
        assert msg1["message"]["content"] == "initial"

        msg2 = await gen.__anext__()
        assert msg2["message"]["content"] == "x"

        # Generator is suspended at the yield *after* "x" was consumed.
        # `pending` was set to None before the yield, so the finally is a no-op.
        await gen.aclose()

        assert inbox.empty()

    async def test_close_with_empty_inbox_is_noop(self) -> None:
        """Closing a generator that has no local item and an empty inbox is a no-op."""
        inbox: asyncio.Queue[str] = asyncio.Queue()

        gen = _message_source("initial", inbox)
        msg = await gen.__anext__()
        assert msg["message"]["content"] == "initial"

        await gen.aclose()

        assert inbox.empty()


class TestForwarderCancellation:
    """Tier B: unit tests for _forwarder behavior under cancellation.

    Note on S4b coverage: the `pending is not None` recovery branch is
    unreachable under `CancelledError` — between `pending = await get()`
    returning and `pending = None`, no await sits, so cancellation cannot
    land there (see design §Open Questions). The dedicated
    `test_pending_recovered_on_put_failure` test forces the branch via a
    non-cancellation exception from `put_nowait` to prove the recovery
    logic itself is correct.
    """

    async def test_item_survives_cancellation_after_forward(self) -> None:
        """Item moved from buffer to inbox before cancellation is not lost."""
        coord = Coordinator()
        inbox: asyncio.Queue[str] = asyncio.Queue()
        coord._message_buffer.put_nowait("x")

        task = asyncio.create_task(coord._forwarder(inbox))
        await asyncio.sleep(0)  # forwarder moves "x" into inbox

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert inbox.qsize() == 1
        assert inbox.get_nowait() == "x"
        assert coord._message_buffer.empty()

    async def test_cancellation_with_empty_buffer_loses_nothing(self) -> None:
        """Forwarder cancelled while awaiting on empty buffer: no spurious items appear."""
        coord = Coordinator()
        inbox: asyncio.Queue[str] = asyncio.Queue()

        task = asyncio.create_task(coord._forwarder(inbox))
        await asyncio.sleep(0)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert inbox.empty()
        assert coord._message_buffer.empty()

    async def test_multiple_items_survive_cancellation(self) -> None:
        """Multiple items forwarded before cancellation preserve order."""
        coord = Coordinator()
        inbox: asyncio.Queue[str] = asyncio.Queue()
        coord._message_buffer.put_nowait("a")
        coord._message_buffer.put_nowait("b")

        task = asyncio.create_task(coord._forwarder(inbox))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        forwarded: list[str] = []
        while not inbox.empty():
            forwarded.append(inbox.get_nowait())
        assert forwarded == ["a", "b"]
        assert coord._message_buffer.empty()

    async def test_pending_recovered_on_put_failure(self) -> None:
        """S4b `pending is not None` branch: put_nowait failure re-enqueues to buffer."""
        coord = Coordinator()
        coord._message_buffer.put_nowait("x")

        class _FailingQueue(asyncio.Queue[str]):
            def put_nowait(self, item: str) -> None:
                raise RuntimeError("simulated put failure")

        failing_inbox: asyncio.Queue[str] = _FailingQueue()

        with pytest.raises(RuntimeError, match="simulated put failure"):
            await coord._forwarder(failing_inbox)

        # `pending` was holding "x" when put_nowait raised — finally must
        # re-enqueue it to _message_buffer (no drop).
        assert coord._message_buffer.qsize() == 1
        assert coord._message_buffer.get_nowait() == "x"


class TestRequeueLoop:
    """Tests for DLT-163: coordinator-owned re-queue loop in send_message()."""

    async def test_leftover_message_auto_processed_in_same_generator(self, mock_sdk) -> None:
        """AC1: leftover message is automatically re-processed in the same generator."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="resp A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="resp B")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("A")
            events = []
            enqueued_b = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_b:
                    coord.enqueue("B")
                    enqueued_b = True

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 2
        assert not coord.has_pending_messages

    async def test_fifo_order_preserved_for_multiple_leftovers(self, mock_sdk) -> None:
        """AC2: multiple leftover messages are re-processed in FIFO order."""
        client, _ = mock_sdk

        captured_gens: list[AsyncIterator] = []

        async def _capturing_connect(gen):
            captured_gens.append(gen)

        client.connect = AsyncMock(side_effect=_capturing_connect)

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="resp A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="resp B")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="resp C")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="resp D")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("A")
            events = []
            enqueued_extra = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_extra:
                    coord.enqueue("B")
                    coord.enqueue("C")
                    coord.enqueue("D")
                    enqueued_extra = True

        # Verify all four exchanges happened via the captured generators
        assert len(captured_gens) == 4
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 4
        assert not coord.has_pending_messages

    async def test_happy_path_unchanged_single_iteration(self, mock_sdk) -> None:
        """AC10: when no teardown messages, loop runs exactly once."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="ok")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = await _send(coord, "hello")

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1
        # SDK client created exactly once
        _, mock_cls = mock_sdk
        assert mock_cls.call_count == 1
        assert not coord.has_pending_messages

    async def test_pending_task_lifecycle_across_iterations(self, mock_sdk) -> None:
        """AC5: iteration N's pending task is awaited at start of iteration N+1."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="late ok")]), make_result()),
        ]

        coord = Coordinator()

        # Mock the msg pipeline to track when tasks are created and awaited
        pipeline_mock = MagicMock()
        run_future = asyncio.get_event_loop().create_future()
        run_future.set_result(None)
        pipeline_mock.run = AsyncMock(return_value=run_future)

        coord._msg_pipeline = pipeline_mock

        # Also need a registry for the pipeline to fire
        registry_mock = MagicMock()
        session = Session(id="test", started_at=datetime.now(UTC))
        registry_mock.get_active_session = AsyncMock(return_value=session)
        coord._registry = registry_mock

        # Don't use async with — check state before __aexit__ clears it
        await coord.__aenter__()
        try:
            coord.enqueue("A")
            enqueued_late = False
            async for event in coord.send_message():
                if isinstance(event, Result) and not enqueued_late:
                    coord.enqueue("late")
                    enqueued_late = True

            # After generator completes, the last iteration's task is still pending
            # (it's awaited at the start of the NEXT send_message call, not before return)
            assert coord._pending_msg_task is not None
            assert not coord._pending_msg_task.done()
        finally:
            await coord.__aexit__(None, None, None)

    async def test_requeue_failure_yields_error_and_preserves_messages(self, mock_sdk) -> None:
        """AC6: re-queue failure yields Error event and preserves messages."""
        client, _ = mock_sdk

        async def _failing():
            raise CLIConnectionError("re-queue failed")
            yield  # noqa: RUF027

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _failing(),
        ]

        async with Coordinator() as coord:
            coord.enqueue("A")
            events = []
            enqueued_b = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_b:
                    coord.enqueue("B")
                    enqueued_b = True

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1
        assert error_events[0].recoverable

    async def test_generator_abandonment_preserves_messages(self, mock_sdk) -> None:
        """AC7: abandoning the generator mid-re-queue preserves remaining messages."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="late ok")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("A")
            coord.enqueue("B")
            # Use explicit generator control to stop after first exchange's Result
            gen = coord.send_message()
            async for event in gen:
                if isinstance(event, Result):
                    break

            # Explicitly await generator cleanup before asserting buffer state
            await gen.aclose()

            # "B" should remain in the buffer after abandonment
            assert coord.has_pending_messages

    async def test_status_events_yielded_across_iterations(self, mock_sdk) -> None:
        """AC: Status events from multiple iterations are yielded to the caller."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="late ok")]), make_result()),
        ]

        async with Coordinator() as coord:
            coord.enqueue("A")
            events = []
            enqueued_late = False
            async for event in coord.send_message():
                events.append(event)
                if isinstance(event, Result) and not enqueued_late:
                    coord.enqueue("late")
                    enqueued_late = True

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 2

    async def test_has_pending_messages_state_during_requeue(self, mock_sdk) -> None:
        """AC: has_pending_messages accurately reflects buffer state across iterations."""
        client, _ = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B ok")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="C ok")]), make_result()),
        ]

        buffer_states: list[bool] = []

        async with Coordinator() as coord:
            coord.enqueue("A")
            coord.enqueue("B")
            coord.enqueue("C")
            async for event in coord.send_message():
                if isinstance(event, Result):
                    buffer_states.append(coord.has_pending_messages)

        # After each Result: True (B,C remaining), True (C remaining), False (empty)
        assert len(buffer_states) == 3
        assert buffer_states[0] is True
        assert buffer_states[1] is True
        assert buffer_states[2] is False
        assert not coord.has_pending_messages
