"""Coordinator integration tests.

Tests for DLT-001: Core agent architecture.
Tests for DLT-027: Session tracking integration.
Tests for DLT-008: Post-processing pipeline integration.
Mocks ClaudeSDKClient to test the coordinator's end-to-end behavior.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_agent_sdk import CLIConnectionError, ProcessError
from claude_agent_sdk.types import (
    AgentDefinition,
    SystemMessage,
    TextBlock,
    ToolUseBlock,
    UserMessage,
)
from helpers import make_assistant, make_result

from tachikoma.coordinator import Coordinator, _derive_transcript_path
from tachikoma.events import Error, Result, TextChunk, ToolActivity
from tachikoma.pre_processing import ContextResult
from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import Session


async def _mock_messages(*messages):
    for msg in messages:
        yield msg


@pytest.fixture
def mock_sdk(mocker):
    """Mock the ClaudeSDKClient class.

    The coordinator now creates a fresh ``async with ClaudeSDKClient(options)``
    per ``send_message()`` call, so we mock the class to return a mock client
    whose ``__aenter__`` yields itself.
    """
    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.query = AsyncMock()
    mock_client.interrupt = AsyncMock()
    mock_client.receive_response = MagicMock()

    mock_cls = mocker.patch(
        "tachikoma.coordinator.ClaudeSDKClient", return_value=mock_client,
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
        client.__aenter__.assert_not_awaited()
        client.__aexit__.assert_not_awaited()

    async def test_send_message_creates_client_per_call(self, mock_sdk) -> None:
        """Each send_message() creates a fresh ClaudeSDKClient via async with."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="A")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = [e async for e in coord.send_message("first")]

            client.receive_response.return_value = _mock_messages(
                make_assistant([TextBlock(text="B")]),
                make_result(),
            )
            _ = [e async for e in coord.send_message("second")]

        # Two send_message calls → two ClaudeSDKClient instantiations
        assert mock_cls.call_count == 2
        assert client.__aenter__.await_count == 2
        assert client.__aexit__.await_count == 2

    async def test_connect_failure_in_send_message_yields_error(self, mock_sdk) -> None:
        """Client creation failure inside send_message() yields a recoverable Error."""
        client, _ = mock_sdk
        client.__aenter__.side_effect = CLIConnectionError("no CLI")

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("hello")]

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
            events = [e async for e in coord.send_message("hi")]

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
            events = [e async for e in coord.send_message("read main.py")]

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
            events = [e async for e in coord.send_message("do it")]

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
            events = [e async for e in coord.send_message("search")]

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
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.allowed_tools == ["Read", "Glob"]

    async def test_forwards_cwd_to_sdk_options(self, mock_sdk) -> None:
        """AC (R8, DLT-023): Coordinator passes cwd to ClaudeAgentOptions."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(cwd=Path("/workspace")) as coord:
            _ = [e async for e in coord.send_message("hello")]

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
            events = [e async for e in coord.send_message("hello")]

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

        client.receive_response.side_effect = [_failing(), _ok()]

        async with Coordinator() as coord:
            events1 = [e async for e in coord.send_message("first")]
            events2 = [e async for e in coord.send_message("second")]

        assert isinstance(events1[-1], Error)

        text_events = [e for e in events2 if isinstance(e, TextChunk)]
        assert text_events[0].text == "recovered"


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
                return [e async for e in coord.send_message("hi")]

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
    registry.close_session = AsyncMock()
    registry.update_metadata = AsyncMock()
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
            _ = [e async for e in coord.send_message("hello")]

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
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

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
            _ = [e async for e in coord.send_message("hello")]

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
            events = [e async for e in coord.send_message("hi")]

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
            events = [e async for e in coord.send_message("hi")]

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
        self, mock_sdk,
    ) -> None:
        """AC: Given system_prompt is provided -> system_prompt is a SystemPromptPreset."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(system_prompt="Custom prompt") as coord:
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        assert options.system_prompt["type"] == "preset"
        assert options.system_prompt["preset"] == "claude_code"
        assert options.system_prompt["append"] == "Custom prompt"

    async def test_system_prompt_none_leaves_unset(
        self, mock_sdk,
    ) -> None:
        """AC: Given system_prompt is None -> ClaudeAgentOptions.system_prompt is None."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.system_prompt is None

    async def test_system_prompt_does_not_break_send_message(self, mock_sdk) -> None:
        """AC: Given system_prompt is provided -> existing coordinator behavior still works."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        async with Coordinator(system_prompt="Custom prompt") as coord:
            events = [e async for e in coord.send_message("hi")]

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
            _ = [e async for e in coord.send_message("hello")]

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
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.permission_mode is None

    async def test_env_passed_to_sdk_options(self, mock_sdk) -> None:
        """AC: Given env is provided -> ClaudeAgentOptions.env is set."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator(env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}) as coord:
            _ = [e async for e in coord.send_message("hello")]

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
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.env == {}


def _make_mock_pipeline():
    """Create a mock PostProcessingPipeline with sensible defaults."""
    pipeline = MagicMock()
    pipeline.run = AsyncMock()
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

        async def track_close(session_id: str) -> None:
            call_order.append("close")

        async def track_run(session: Session) -> None:
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

    async def test_calls_on_status_before_pipeline_run(self, mock_sdk) -> None:
        """AC4: Status callback is called before pipeline runs."""
        active = Session(
            id="s7",
            started_at=datetime.now(UTC),
            sdk_session_id="sdk-status",
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()

        call_order: list[str] = []
        on_status = MagicMock(side_effect=lambda msg: call_order.append("status"))
        pipeline.run.side_effect = AsyncMock(side_effect=lambda s: call_order.append("pipeline"))

        async with Coordinator(
            registry=registry, pipeline=pipeline, on_status=on_status,
        ):
            pass

        on_status.assert_called_once_with("Processing memories...")
        assert call_order == ["status", "pipeline"]

    async def test_on_status_not_called_without_pipeline(self, mock_sdk) -> None:
        """AC: Status callback not called when no pipeline is registered."""
        on_status = MagicMock()

        async with Coordinator(on_status=on_status):
            pass

        on_status.assert_not_called()

    async def test_on_status_not_called_without_sdk_session_id(self, mock_sdk) -> None:
        """AC: Status callback not called when session has no sdk_session_id."""
        active = Session(
            id="s8",
            started_at=datetime.now(UTC),
            sdk_session_id=None,
        )
        registry = _make_mock_registry(active_session=active)
        pipeline = _make_mock_pipeline()
        on_status = MagicMock()

        async with Coordinator(
            registry=registry, pipeline=pipeline, on_status=on_status,
        ):
            pass

        on_status.assert_not_called()


class TestCoordinatorSteering:
    """Tests for DLT-002: steering mechanism in the coordinator."""

    async def test_steer_calls_client_query(self, mock_sdk) -> None:
        """AC: steer() calls client.query() with the text during send_message."""
        client, _ = mock_sdk

        steered = asyncio.Event()

        async def _slow_messages():
            yield make_assistant([TextBlock(text="thinking...")])
            await steered.wait()
            yield make_result()

        client.receive_response.return_value = _slow_messages()

        async with Coordinator() as coord:

            async def consume():
                return [e async for e in coord.send_message("hi")]

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            await coord.steer("follow-up message")
            steered.set()
            await task

        # query is called twice: once for the initial send_message, once for steer
        assert client.query.await_count == 2
        assert client.query.call_args_list[1][0][0] == "follow-up message"

    async def test_steer_increments_pending_counter(self, mock_sdk) -> None:
        """AC: steer() increments _pending_steers counter."""
        client, _ = mock_sdk

        steered = asyncio.Event()

        async def _slow_messages():
            yield make_assistant([TextBlock(text="thinking...")])
            await steered.wait()
            yield make_result()

        client.receive_response.return_value = _slow_messages()

        async with Coordinator() as coord:

            async def consume():
                return [e async for e in coord.send_message("hi")]

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            assert coord._pending_steers == 0
            await coord.steer("message 1")
            assert coord._pending_steers == 1
            await coord.steer("message 2")
            assert coord._pending_steers == 2

            steered.set()
            await task

    async def test_steer_without_active_client_raises_runtime_error(self, mock_sdk) -> None:
        """AC: steer() without active client raises RuntimeError."""
        coord = Coordinator()

        with pytest.raises(RuntimeError, match="not connected"):
            await coord.steer("message")

    async def test_send_message_processes_steered_responses(self, mock_sdk) -> None:
        """AC: send_message() handles additional receive_response() calls for steered messages."""
        client, _ = mock_sdk

        # First receive_response: initial response
        # Second receive_response: steered response
        client.receive_response.side_effect = [
            _mock_messages(
                make_assistant([TextBlock(text="A")]),
                make_result(),
            ),
            _mock_messages(
                make_assistant([TextBlock(text="B")]),
                make_result(),
            ),
        ]

        async with Coordinator() as coord:
            # Pre-set pending steer so the loop fires a second receive_response
            coord._pending_steers = 1

            events = [e async for e in coord.send_message("initial")]

        # Should see events from both responses
        result_events = [e for e in events if isinstance(e, Result)]
        text_events = [e for e in events if isinstance(e, TextChunk)]

        assert len(result_events) == 2
        assert len(text_events) == 2
        assert text_events[0].text == "A"
        assert text_events[1].text == "B"

    async def test_send_message_breaks_after_final_result(self, mock_sdk) -> None:
        """AC: send_message() breaks after final Result when counter reaches 0."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="response")]),
            make_result(),
        )

        async with Coordinator() as coord:
            events = [e async for e in coord.send_message("initial")]

        # Should see exactly one Result
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1

    async def test_pending_steers_decremented_on_each_response(self, mock_sdk) -> None:
        """AC: _pending_steers is decremented on each steered receive_response."""
        client, _ = mock_sdk

        # Three sequential receive_response calls: initial + 2 steered
        client.receive_response.side_effect = [
            _mock_messages(
                make_assistant([TextBlock(text="A")]),
                make_result(),
            ),
            _mock_messages(
                make_assistant([TextBlock(text="B")]),
                make_result(),
            ),
            _mock_messages(
                make_assistant([TextBlock(text="C")]),
                make_result(),
            ),
        ]

        async with Coordinator() as coord:
            # Queue two steers
            coord._pending_steers = 2

            events = [e async for e in coord.send_message("initial")]

            # Counter should be 0 after all responses
            assert coord._pending_steers == 0

        # Should see three Result events
        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 3


class TestCoordinatorAgents:
    """Tests for DLT-003: sub-agent delegation via agents parameter."""

    async def test_passes_agents_to_sdk_options(self, mock_sdk) -> None:
        """AC: Given agents dict -> ClaudeAgentOptions.agents is set."""
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

        async with Coordinator(agents=agents) as coord:
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.agents == agents

    async def test_no_agents_when_none_provided(self, mock_sdk) -> None:
        """AC: Given agents=None -> ClaudeAgentOptions.agents is None."""
        client, mock_cls = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="hi")]),
            make_result(),
        )

        async with Coordinator() as coord:
            _ = [e async for e in coord.send_message("hello")]

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

        async with Coordinator(agents=agents) as coord:
            _ = [e async for e in coord.send_message("hello")]

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

        async with Coordinator(agents=agents) as coord:
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.agents["analysis/deep"].model == "opus"

    async def test_agents_does_not_break_send_message(self, mock_sdk) -> None:
        """AC: Given agents are provided -> existing coordinator behavior still works."""
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

        async with Coordinator(agents=agents) as coord:
            events = [e async for e in coord.send_message("hi")]

        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello!"

    async def test_agents_preserved_across_messages(self, mock_sdk) -> None:
        """AC: agents are preserved across multiple send_message() calls."""
        client, mock_cls = mock_sdk

        agents = {
            "test/agent": AgentDefinition(
                description="Test agent",
                prompt="A test agent.",
            ),
        }

        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="a")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="b")]), make_result()),
        ]

        async with Coordinator(agents=agents) as coord:
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

        # Both calls should have agents in options
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
        self, mock_sdk,
    ) -> None:
        """AC: First message of new session triggers pre_pipeline.run()."""
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
            _ = [e async for e in coord.send_message("hello")]

        pre_pipeline.run.assert_awaited_once_with("hello")
        # Verify the enriched message was sent to the SDK
        client.query.assert_awaited_once()
        query_text = client.query.call_args[0][0]
        assert "<memories>" in query_text
        assert "hello" in query_text

    async def test_skips_pre_pipeline_on_subsequent_message(
        self, mock_sdk,
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
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

        # Pre-processing should only run on the first message
        assert pre_pipeline.run.await_count == 1

    async def test_skips_pre_pipeline_when_no_pipeline_provided(
        self, mock_sdk,
    ) -> None:
        """AC: No pre_pipeline means message is sent unmodified."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry) as coord:
            _ = [e async for e in coord.send_message("hello")]

        # Message should be sent without enrichment
        client.query.assert_awaited_once_with("hello")

    async def test_skips_pre_pipeline_when_no_registry(
        self, mock_sdk,
    ) -> None:
        """AC: No registry means pre-processing is skipped."""
        client, _ = mock_sdk
        client.receive_response.return_value = _mock_messages(
            make_assistant([TextBlock(text="Hello!")]),
            make_result(),
        )

        pre_pipeline = _make_mock_pre_pipeline()

        async with Coordinator(pre_pipeline=pre_pipeline) as coord:
            _ = [e async for e in coord.send_message("hello")]

        pre_pipeline.run.assert_not_awaited()
        client.query.assert_awaited_once_with("hello")

    async def test_pre_pipeline_failure_sends_original_message(
        self, mock_sdk,
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
            _ = [e async for e in coord.send_message("hello")]

        # Original message should be sent despite failure
        client.query.assert_awaited_once_with("hello")

    async def test_pre_pipeline_empty_results_sends_original_message(
        self, mock_sdk,
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
            _ = [e async for e in coord.send_message("hello")]

        # Original message should be sent (assemble_context returns original on empty)
        client.query.assert_awaited_once_with("hello")

    async def test_session_creation_failure_skips_pre_pipeline(
        self, mock_sdk,
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
            _ = [e async for e in coord.send_message("hello")]

        pre_pipeline.run.assert_not_awaited()
        client.query.assert_awaited_once_with("hello")


class TestCoordinatorMcpServers:
    """Tests for DLT-030: MCP servers extraction from pre-processing results."""

    async def test_extracts_mcp_servers_from_pre_processing_results(
        self, mock_sdk, mocker
    ) -> None:
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
            ContextResult(tag="projects", content="Project list", mcp_servers={"projects": mock_server}),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = [e async for e in coord.send_message("hello")]

        # Verify options were built with mcp_servers
        mock_cls.assert_called_once()
        options = mock_cls.call_args[0][0]
        assert options.mcp_servers == {"projects": mock_server}

    async def test_merges_mcp_servers_from_multiple_results(
        self, mock_sdk, mocker
    ) -> None:
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
            _ = [e async for e in coord.send_message("hello")]

        options = mock_cls.call_args[0][0]
        assert options.mcp_servers == {"projects": server1, "tools": server2}

    async def test_mcp_servers_persist_across_messages_in_session(
        self, mock_sdk, mocker
    ) -> None:
        """AC: MCP servers persist across messages within the same session."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B")]), make_result()),
        ]

        mock_server = {"type": "sdk", "sdkServer": MagicMock()}
        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(tag="projects", content="Projects", mcp_servers={"projects": mock_server}),
        ]
        registry = _make_mock_registry(active_session=None)

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline) as coord:
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

        # Both calls should have the same mcp_servers
        assert mock_cls.call_count == 2
        options1 = mock_cls.call_args_list[0][0][0]
        options2 = mock_cls.call_args_list[1][0][0]
        assert options1.mcp_servers == {"projects": mock_server}
        assert options2.mcp_servers == {"projects": mock_server}

    async def test_mcp_servers_cleared_on_session_transition(
        self, mock_sdk, mocker
    ) -> None:
        """AC: MCP servers are cleared when transitioning to a new session."""
        client, mock_cls = mock_sdk
        client.receive_response.side_effect = [
            _mock_messages(make_assistant([TextBlock(text="A")]), make_result()),
            _mock_messages(make_assistant([TextBlock(text="B")]), make_result()),
        ]

        mock_server = {"type": "sdk", "sdkServer": MagicMock()}
        pre_pipeline = _make_mock_pre_pipeline()
        pre_pipeline.run.return_value = [
            ContextResult(tag="projects", content="Projects", mcp_servers={"projects": mock_server}),
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
            return_value=False,
        )

        async with Coordinator(registry=registry, pre_pipeline=pre_pipeline, cwd=Path("/ws")) as coord:
            _ = [e async for e in coord.send_message("first")]

            # Update mock for second call - no mcp_servers in new session
            pre_pipeline.run.return_value = []

            _ = [e async for e in coord.send_message("new topic")]

        # First call should have mcp_servers, second call should be empty
        assert mock_cls.call_count == 2
        options1 = mock_cls.call_args_list[0][0][0]
        options2 = mock_cls.call_args_list[1][0][0]
        assert options1.mcp_servers == {"projects": mock_server}
        assert options2.mcp_servers == {}

    async def test_no_mcp_servers_when_not_provided(self, mock_sdk) -> None:
        """AC: No mcp_servers in ContextResult means empty dict in options."""
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
            _ = [e async for e in coord.send_message("hello")]

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

        async with Coordinator(registry=registry, cwd=Path("/workspace")) as coord:
            _ = [e async for e in coord.send_message("hello")]

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

        async with Coordinator(registry=registry, cwd=Path("/workspace")) as coord:
            _ = [e async for e in coord.send_message("hello")]

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
            return_value=True,
        )

        async with Coordinator(registry=registry, cwd=Path("/workspace")) as coord:
            events = [e async for e in coord.send_message("tell me more")]

        # Should NOT trigger transition (close_session called only once at shutdown)
        mock_detect.assert_awaited_once()
        # close_session should be called exactly once (at shutdown, not during transition)
        assert registry.close_session.await_count == 1
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "continuing"

    async def test_boundary_detection_error_defaults_to_continuation(
        self, mock_sdk, mocker,
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

        async with Coordinator(registry=registry, cwd=Path("/workspace")) as coord:
            events = [e async for e in coord.send_message("hello")]

        # Error should be caught, message should still be processed
        mock_detect.assert_awaited_once()
        # close_session should be called exactly once (at shutdown, not during transition)
        assert registry.close_session.await_count == 1
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert text_events[0].text == "still works"

    async def test_awaits_pending_task_before_detection(
        self, mock_sdk, mocker,
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

        async def slow_msg_pipeline(session, user_msg, agent_response):
            call_order.append("msg_pipeline_start")
            await asyncio.sleep(0.05)
            call_order.append("msg_pipeline_end")

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock(side_effect=slow_msg_pipeline)

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=True,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
        ) as coord:
            # First message triggers per-message pipeline
            _ = [e async for e in coord.send_message("first")]
            # Give the background task time to start
            await asyncio.sleep(0.01)
            # Second message should await pending task before detection
            _ = [e async for e in coord.send_message("second")]

        # The pending task should have been awaited before detection
        assert "msg_pipeline_start" in call_order
        assert "msg_pipeline_end" in call_order

    async def test_pending_task_failure_logged_not_propagated(
        self, mock_sdk, mocker,
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
            return_value=True,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("first")]
            # Second message should not raise despite pending task failure
            _ = [e async for e in coord.send_message("second")]

        # Detection should still have been called
        mock_detect.assert_awaited()


class TestSessionTransition:
    """Tests for DLT-026: session transition on topic shift."""

    async def test_closes_current_session_on_topic_shift(
        self, mock_sdk, mocker,
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
            return_value=False,  # Topic shift
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            _ = [e async for e in coord.send_message("what's for dinner?")]

        registry.close_session.assert_awaited_once_with("s1")

    async def test_fires_async_session_post_processing(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        task_completed = asyncio.Event()

        async def track_pipeline_run(session):
            task_completed.set()

        pipeline.run.side_effect = track_pipeline_run

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            pipeline=pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]
            # Give background task time to start
            await asyncio.sleep(0.05)

        # Pipeline should have been called with the session
        pipeline.run.assert_awaited_once()
        session_arg = pipeline.run.call_args[0][0]
        assert session_arg.id == "s1"

    async def test_skips_session_post_processing_when_no_sdk_session_id(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            pipeline=pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

        # Pipeline should NOT have been called for transition
        pipeline.run.assert_not_awaited()

    async def test_clears_sdk_session_id_on_topic_shift(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

            # After transition, _sdk_session_id should be None
            assert coord._sdk_session_id is None

    async def test_stores_previous_summary_on_topic_shift(
        self, mock_sdk, mocker,
    ) -> None:
        """AC: Topic shift stores previous session's summary in _previous_summary."""
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            # _previous_summary is consumed during _build_options in send_message,
            # but we can verify the transition set it by checking the options used
            _ = [e async for e in coord.send_message("new topic")]

            # After _build_options consumed it, it should be None again
            assert coord._previous_summary is None

    async def test_creates_new_session_after_transition(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

        # New session should be created
        registry.create_session.assert_awaited()

    async def test_session_close_error_does_not_block_transition(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

        # Despite close error, new session should be created
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
            _ = [e async for e in coord.send_message("first")]
            _ = [e async for e in coord.send_message("second")]

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
            _ = [e async for e in coord.send_message("hello")]

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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
        ) as coord:
            # Seed _sdk_session_id as if there was a previous message in that session
            coord._sdk_session_id = "sdk-old"
            _ = [e async for e in coord.send_message("new topic")]

        # After topic shift, options.resume should be None (new session)
        options = mock_cls.call_args[0][0]
        assert options.resume is None

    async def test_previous_summary_injected_into_system_prompt(
        self, mock_sdk, mocker,
    ) -> None:
        """AC: Previous conversation summary is injected into system prompt after topic shift."""
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            system_prompt="Base prompt",
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

        # The options used for the message should have the summary in the system prompt
        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        append_text = options.system_prompt["append"]
        assert "Python testing frameworks" in append_text
        assert "Base prompt" in append_text

    async def test_previous_summary_with_none_base_prompt(
        self, mock_sdk, mocker,
    ) -> None:
        """AC: When base system prompt is None, only summary section is used."""
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
        registry = _make_mock_registry(active_session=active)
        registry.get_active_session.side_effect = [active, None, None]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            system_prompt=None,  # No base prompt
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]

        # Should still have a system prompt with the summary
        options = mock_cls.call_args[0][0]
        assert options.system_prompt is not None
        append_text = options.system_prompt["append"]
        assert "Summary text" in append_text

    async def test_previous_summary_cleared_after_first_use(
        self, mock_sdk, mocker,
    ) -> None:
        """AC: Previous summary is cleared after the first message of the new session."""
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
        registry.get_active_session.side_effect = [
            active, None, new_session, new_session, new_session,
        ]

        mocker.patch(
            "tachikoma.coordinator.detect_boundary",
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            system_prompt="Base prompt",
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]
            _ = [e async for e in coord.send_message("follow-up")]

        # First message after shift: summary injected
        first_options = mock_cls.call_args_list[0][0][0]
        assert "Previous summary" in first_options.system_prompt["append"]

        # Second message: summary already consumed, not injected again
        second_options = mock_cls.call_args_list[1][0][0]
        assert second_options.system_prompt["append"] == "Base prompt"


class TestPerMessagePostProcessing:
    """Tests for DLT-026: per-message post-processing pipeline trigger."""

    async def test_triggers_msg_pipeline_after_result(
        self, mock_sdk,
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
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("hello")]

        # Give the background task time to be scheduled
        await asyncio.sleep(0.05)

        msg_pipeline.run.assert_awaited_once()

    async def test_passes_accumulated_response_text(
        self, mock_sdk,
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
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("hello")]

        await asyncio.sleep(0.05)

        # Check that the accumulated text was passed
        call_args = msg_pipeline.run.call_args
        agent_response = call_args[0][2]  # Third positional argument
        assert agent_response == "Hello there!"

    async def test_skips_pipeline_when_no_msg_pipeline(
        self, mock_sdk,
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
            cwd=Path("/workspace"),
        ) as coord:
            _ = [e async for e in coord.send_message("hello")]

    async def test_pipeline_receives_current_session(
        self, mock_sdk,
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
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("hello")]

        await asyncio.sleep(0.05)

        # Check that the pipeline received the session
        call_args = msg_pipeline.run.call_args
        session_arg = call_args[0][0]
        assert session_arg.id == "s1"


class TestCoordinatorShutdownWithBoundaryDetection:
    """Tests for DLT-026: shutdown with background tasks from boundary detection."""

    async def test_awaits_pending_msg_task_on_shutdown(
        self, mock_sdk,
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

        async def slow_pipeline(session, user_msg, agent_response):
            await asyncio.sleep(0.05)
            task_completed.set()

        msg_pipeline = MagicMock()
        msg_pipeline.run = AsyncMock(side_effect=slow_pipeline)

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            msg_pipeline=msg_pipeline,
            pipeline=_make_mock_pipeline(),
        ) as coord:
            _ = [e async for e in coord.send_message("hello")]
            # Exit immediately while task is pending

        # The task should have been awaited and completed
        assert task_completed.is_set()

    async def test_awaits_background_tasks_on_shutdown(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            pipeline=pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]
            # Exit while background task is running

        # The background task should have been awaited
        assert task_completed.is_set()

    async def test_background_task_failure_does_not_block_shutdown(
        self, mock_sdk, mocker,
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
            return_value=False,
        )

        # Should not raise despite background task failure
        async with Coordinator(
            registry=registry,
            cwd=Path("/workspace"),
            pipeline=pipeline,
        ) as coord:
            _ = [e async for e in coord.send_message("new topic")]
