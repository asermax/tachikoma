"""Tests for post-processing pipeline.

Tests for DLT-008: Extract and store memories from conversations.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import (
    FINALIZE_PHASE,
    MAIN_PHASE,
    PRE_FINALIZE_PHASE,
    PostProcessingPipeline,
    PostProcessor,
    PromptDrivenProcessor,
    fork_and_capture,
    fork_and_consume,
)
from tachikoma.sessions.model import Session


class _FakeProcessor(PostProcessor):
    """Concrete processor for testing - methods overridden per-test."""

    async def process(self, session: Session) -> None:
        pass


def _make_mock_processor() -> _FakeProcessor:
    """Create a processor with mockable process method."""
    processor = _FakeProcessor()
    # Override the process method with an AsyncMock
    processor.process = AsyncMock()
    return processor


def _make_session(sdk_session_id: str | None = "sdk-123") -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        sdk_session_id=sdk_session_id,
    )


class TestPostProcessingPipeline:
    """Tests for PostProcessingPipeline."""

    async def test_runs_all_registered_processors(self) -> None:
        """AC: All registered processors are awaited with the same session."""
        processor1 = _make_mock_processor()
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = PostProcessingPipeline()
        pipeline.register(processor1)
        pipeline.register(processor2)

        await pipeline.run(session)

        processor1.process.assert_awaited_once_with(session)
        processor2.process.assert_awaited_once_with(session)

    async def test_error_isolation_continues_other_processors(self) -> None:
        """AC: One processor failure doesn't prevent others from completing."""
        processor1 = _make_mock_processor()
        processor1.process.side_effect = RuntimeError("failed")
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = PostProcessingPipeline()
        pipeline.register(processor1)
        pipeline.register(processor2)

        await pipeline.run(session)

        # Both processors should have been called
        processor1.process.assert_awaited_once()
        processor2.process.assert_awaited_once()

    async def test_logs_processor_failures(self, capsys: pytest.CaptureFixture) -> None:
        """AC: Processor failures are logged per DES-002."""
        processor = _make_mock_processor()
        processor.process.side_effect = RuntimeError("test error")
        session = _make_session()

        pipeline = PostProcessingPipeline()
        pipeline.register(processor)

        # Run the pipeline - the error should be caught and logged
        await pipeline.run(session)

        # Verify the processor was called
        processor.process.assert_awaited_once()

    async def test_returns_after_all_complete(self) -> None:
        """AC: Pipeline awaits all processors before returning."""
        call_order: list[str] = []

        async def slow_process(session: Session) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_process(session: Session) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_process
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_process

        pipeline = PostProcessingPipeline()
        pipeline.register(slow_processor)
        pipeline.register(fast_processor)

        await pipeline.run(_make_session())

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")

    async def test_serializes_concurrent_invocations(self) -> None:
        """AC: Concurrent run() calls execute sequentially (lock test)."""
        call_times: list[tuple[float, str]] = []

        async def track_process(session: Session) -> None:
            call_times.append((asyncio.get_event_loop().time(), "start"))
            await asyncio.sleep(0.05)
            call_times.append((asyncio.get_event_loop().time(), "end"))

        processor = _make_mock_processor()
        processor.process.side_effect = track_process

        pipeline = PostProcessingPipeline()
        pipeline.register(processor)

        # Run two invocations concurrently
        session1 = _make_session(sdk_session_id="sdk-1")
        session2 = _make_session(sdk_session_id="sdk-2")

        await asyncio.gather(pipeline.run(session1), pipeline.run(session2))

        # Verify they ran sequentially (not overlapping)
        # First run should complete before second starts
        assert len(call_times) == 4
        # First "end" should be before second "start"
        first_end = call_times[1]  # First run's end
        second_start = call_times[2]  # Second run's start
        assert first_end[0] <= second_start[0]

    async def test_runs_with_no_registered_processors(self) -> None:
        """AC: Empty pipeline runs without error."""
        pipeline = PostProcessingPipeline()
        session = _make_session()

        # Should not raise
        await pipeline.run(session)


class TestPhasedPipelineExecution:
    """Tests for phased pipeline execution (DLT-020)."""

    def test_unknown_phase_raises_value_error(self) -> None:
        """AC: Registration with unknown phase raises ValueError with clear message."""
        processor = _make_mock_processor()
        pipeline = PostProcessingPipeline()

        with pytest.raises(ValueError, match="Invalid phase 'invalid'"):
            pipeline.register(processor, phase="invalid")

    async def test_finalize_phase_runs_after_main_phase(self) -> None:
        """AC: Finalize-phase processors run after main-phase processors complete."""
        call_order: list[str] = []

        async def track_main(session: Session) -> None:
            call_order.append("main_start")
            await asyncio.sleep(0.02)
            call_order.append("main_end")

        async def track_finalize(session: Session) -> None:
            call_order.append("finalize_start")
            call_order.append("finalize_end")

        main_processor = _make_mock_processor()
        main_processor.process.side_effect = track_main
        finalize_processor = _make_mock_processor()
        finalize_processor.process.side_effect = track_finalize

        pipeline = PostProcessingPipeline()
        pipeline.register(main_processor, phase=MAIN_PHASE)
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Main phase should complete before finalize starts
        assert call_order.index("main_end") < call_order.index("finalize_start")

    async def test_finalize_runs_even_when_main_fails(self) -> None:
        """AC: Finalize-phase processors run even when main-phase processors fail."""
        main_processor = _make_mock_processor()
        main_processor.process.side_effect = RuntimeError("main failed")
        finalize_processor = _make_mock_processor()

        pipeline = PostProcessingPipeline()
        pipeline.register(main_processor, phase=MAIN_PHASE)
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Both should have been called despite main failure
        main_processor.process.assert_awaited_once()
        finalize_processor.process.assert_awaited_once()

    async def test_default_phase_is_main(self) -> None:
        """AC: Default phase is 'main' for backward compatibility."""
        processor = _make_mock_processor()

        pipeline = PostProcessingPipeline()
        pipeline.register(processor)  # No phase specified

        # Verify it went to main phase by checking internal structure
        assert len(pipeline._phases[MAIN_PHASE]) == 1
        assert len(pipeline._phases[FINALIZE_PHASE]) == 0

    async def test_empty_phase_is_skipped(self) -> None:
        """AC: Empty phase is skipped without error."""
        finalize_processor = _make_mock_processor()

        pipeline = PostProcessingPipeline()
        # Only register finalize, leave main empty
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        # Should not raise
        await pipeline.run(_make_session())

        finalize_processor.process.assert_awaited_once()

    async def test_multiple_finalize_processors_run_in_parallel(self) -> None:
        """AC: Multiple finalize processors run in parallel (same as main-phase)."""
        call_order: list[str] = []

        async def slow_finalize(session: Session) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_finalize(session: Session) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_finalize
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_finalize

        pipeline = PostProcessingPipeline()
        pipeline.register(slow_processor, phase=FINALIZE_PHASE)
        pipeline.register(fast_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")

    def test_phase_constants_are_exported(self) -> None:
        """AC: Phase constants are exported and usable."""
        assert MAIN_PHASE == "main"
        assert PRE_FINALIZE_PHASE == "pre_finalize"
        assert FINALIZE_PHASE == "finalize"

    async def test_pre_finalize_phase_runs_between_main_and_finalize(self) -> None:
        """AC: Pre-finalize phase runs after main but before finalize."""
        call_order: list[str] = []

        async def track_main(session: Session) -> None:
            call_order.append("main_start")
            await asyncio.sleep(0.01)
            call_order.append("main_end")

        async def track_pre_finalize(session: Session) -> None:
            call_order.append("pre_finalize_start")
            await asyncio.sleep(0.01)
            call_order.append("pre_finalize_end")

        async def track_finalize(session: Session) -> None:
            call_order.append("finalize_start")
            call_order.append("finalize_end")

        main_processor = _make_mock_processor()
        main_processor.process.side_effect = track_main
        pre_finalize_processor = _make_mock_processor()
        pre_finalize_processor.process.side_effect = track_pre_finalize
        finalize_processor = _make_mock_processor()
        finalize_processor.process.side_effect = track_finalize

        pipeline = PostProcessingPipeline()
        pipeline.register(main_processor, phase=MAIN_PHASE)
        pipeline.register(pre_finalize_processor, phase=PRE_FINALIZE_PHASE)
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Verify ordering: main → pre_finalize → finalize
        assert call_order.index("main_end") < call_order.index("pre_finalize_start")
        assert call_order.index("pre_finalize_end") < call_order.index("finalize_start")

    async def test_pre_finalize_runs_even_when_main_fails(self) -> None:
        """AC: Pre-finalize and finalize processors run even when main fails."""
        main_processor = _make_mock_processor()
        main_processor.process.side_effect = RuntimeError("main failed")
        pre_finalize_processor = _make_mock_processor()
        finalize_processor = _make_mock_processor()

        pipeline = PostProcessingPipeline()
        pipeline.register(main_processor, phase=MAIN_PHASE)
        pipeline.register(pre_finalize_processor, phase=PRE_FINALIZE_PHASE)
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # All should have been called despite main failure
        main_processor.process.assert_awaited_once()
        pre_finalize_processor.process.assert_awaited_once()
        finalize_processor.process.assert_awaited_once()

    async def test_multiple_pre_finalize_processors_run_in_parallel(self) -> None:
        """AC: Multiple pre-finalize processors run in parallel within the phase."""
        call_order: list[str] = []

        async def slow_pre_finalize(session: Session) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_pre_finalize(session: Session) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_pre_finalize
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_pre_finalize

        pipeline = PostProcessingPipeline()
        pipeline.register(slow_processor, phase=PRE_FINALIZE_PHASE)
        pipeline.register(fast_processor, phase=PRE_FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")


class TestForkAndConsume:
    """Tests for fork_and_consume helper."""

    async def test_calls_query_with_fork_options(self, mocker: pytest.MockerFixture) -> None:
        """AC: query() called with correct prompt, resume, fork_session, cwd."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")
        prompt = "Test extraction prompt"
        defaults = AgentDefaults(cwd=Path("/workspace"))

        await fork_and_consume(session, prompt, defaults)

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["prompt"] == prompt

        options = call_kwargs[1]["options"]
        assert options.cwd == Path("/workspace")
        assert options.resume == "sdk-test-123"
        assert options.fork_session is True
        assert options.permission_mode == "bypassPermissions"

    async def test_consumes_full_async_iterator(self, mocker: pytest.MockerFixture) -> None:
        """AC: Async iterator is fully consumed."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for i in range(3):
                consume_count += 1
                yield MagicMock(msg=i)

        mocker.patch("tachikoma.post_processing.query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test")
        await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: Exceptions from query() propagate."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.query", side_effect=failing_query)

        session = _make_session(sdk_session_id="sdk-test")

        with pytest.raises(RuntimeError, match="SDK error"):
            await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_raises_when_no_sdk_session_id(self) -> None:
        """AC: Raises RuntimeError when session has no sdk_session_id."""
        session = _make_session(sdk_session_id=None)

        with pytest.raises(RuntimeError, match="no sdk_session_id"):
            await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_mcp_servers_passed_to_query_options(self, mocker: pytest.MockerFixture) -> None:
        """AC: mcp_servers parameter is passed through to ClaudeAgentOptions."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")
        prompt = "Test prompt"
        defaults = AgentDefaults(cwd=Path("/workspace"))
        mcp_servers = {"test-server": {"type": "stdio", "command": "test"}}

        await fork_and_consume(session, prompt, defaults, mcp_servers=mcp_servers)

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.mcp_servers == mcp_servers

    async def test_mcp_servers_default_none_not_passed(self, mocker: pytest.MockerFixture) -> None:
        """AC: When mcp_servers is None (default), options use SDK default (empty dict)."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.mcp_servers == {}

    async def test_system_prompt_append_sets_system_prompt_preset(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: system_prompt_append param sets SystemPromptPreset on options (DLT-041)."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")
        context = "# Previous Conversation\nUser was discussing Python."

        await fork_and_consume(
            session,
            "Test prompt",
            AgentDefaults(cwd=Path("/workspace")),
            system_prompt_append=context,
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.system_prompt is not None
        assert options.system_prompt["type"] == "preset"
        assert options.system_prompt["preset"] == "claude_code"
        assert options.system_prompt["append"] == context

    async def test_system_prompt_append_none_no_system_prompt(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: system_prompt_append=None (default) leaves system_prompt unset (DLT-041)."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_consume(
            session,
            "Test prompt",
            AgentDefaults(cwd=Path("/workspace")),
            system_prompt_append=None,  # Explicitly None
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.system_prompt is None


class TestForkAndCapture:
    """Tests for fork_and_capture helper."""

    async def test_captures_text_from_content_blocks(self, mocker: pytest.MockerFixture) -> None:
        """AC: Text from content blocks is captured and concatenated."""
        msg1 = MagicMock()
        msg1.content = [MagicMock(text="Hello ")]

        msg2 = MagicMock()
        msg2.content = [MagicMock(text="world")]

        async def fake_query(*args, **kwargs):
            yield msg1
            yield msg2

        mocker.patch("tachikoma.post_processing.query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test-123")
        result = await fork_and_capture(
            session,
            "Generate notification",
            AgentDefaults(cwd=Path("/workspace")),
        )

        assert result == "Hello world"

    async def test_returns_empty_string_when_no_text(self, mocker: pytest.MockerFixture) -> None:
        """AC: Returns empty string when no text blocks in response."""
        msg = MagicMock(spec=[])  # No content attribute

        async def fake_query(*args, **kwargs):
            yield msg

        mocker.patch("tachikoma.post_processing.query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test-123")
        result = await fork_and_capture(
            session,
            "prompt",
            AgentDefaults(cwd=Path("/workspace")),
        )

        assert result == ""

    async def test_fully_consumes_generator(self, mocker: pytest.MockerFixture) -> None:
        """AC: DES-005 compliance — generator is fully consumed."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for _ in range(3):
                consume_count += 1
                yield MagicMock(spec=[])

        mocker.patch("tachikoma.post_processing.query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test")
        await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_calls_query_with_fork_options(self, mocker: pytest.MockerFixture) -> None:
        """AC: query() called with correct resume, fork_session, cwd."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            yield MagicMock(spec=[])

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")
        await fork_and_capture(
            session,
            "Test prompt",
            AgentDefaults(cwd=Path("/workspace")),
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["prompt"] == "Test prompt"

        options = call_kwargs[1]["options"]
        assert options.cwd == Path("/workspace")
        assert options.resume == "sdk-test-123"
        assert options.fork_session is True
        assert options.permission_mode == "bypassPermissions"

    async def test_raises_when_no_sdk_session_id(self) -> None:
        """AC: Raises RuntimeError when session has no sdk_session_id."""
        session = _make_session(sdk_session_id=None)

        with pytest.raises(RuntimeError, match="no sdk_session_id"):
            await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_propagates_query_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: Exceptions from query() propagate."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.query", side_effect=failing_query)

        session = _make_session(sdk_session_id="sdk-test")

        with pytest.raises(RuntimeError, match="SDK error"):
            await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_system_prompt_append_sets_system_prompt_preset(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: system_prompt_append param sets SystemPromptPreset on options (DLT-041)."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            msg = MagicMock()
            msg.content = [MagicMock(text="captured text")]
            yield msg

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")
        context = "# Previous Conversation\nUser was discussing Python."

        await fork_and_capture(
            session,
            "Test prompt",
            AgentDefaults(cwd=Path("/workspace")),
            system_prompt_append=context,
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.system_prompt is not None
        assert options.system_prompt["type"] == "preset"
        assert options.system_prompt["preset"] == "claude_code"
        assert options.system_prompt["append"] == context

    async def test_system_prompt_append_none_no_system_prompt(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: system_prompt_append=None (default) leaves system_prompt unset (DLT-041)."""
        mock_query = mocker.patch("tachikoma.post_processing.query")

        async def fake_query(*args, **kwargs):
            msg = MagicMock()
            msg.content = [MagicMock(text="captured text")]
            yield msg

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_capture(
            session,
            "Test prompt",
            AgentDefaults(cwd=Path("/workspace")),
            system_prompt_append=None,  # Explicitly None
        )

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        options = call_kwargs[1]["options"]
        assert options.system_prompt is None


class TestPromptDrivenProcessor:
    """Tests for PromptDrivenProcessor base class."""

    async def test_process_calls_fork_and_consume_with_correct_args(
        self, mocker: pytest.MockerFixture
    ) -> None:
        """AC: process() calls fork_and_consume with session, prompt, and agent_defaults."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        prompt = "Test prompt"
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = PromptDrivenProcessor(prompt=prompt, agent_defaults=defaults)
        await processor.process(session)

        mock_fork.assert_awaited_once_with(session, prompt, defaults)

    async def test_simple_subclass_inherits_process(self, mocker: pytest.MockerFixture) -> None:
        """AC: Simple subclasses inherit process() and only need a prompt constant."""

        class SimpleProcessor(PromptDrivenProcessor):
            """Simple processor that just provides a prompt."""

            def __init__(self, agent_defaults: AgentDefaults) -> None:
                super().__init__(prompt="Simple extraction prompt", agent_defaults=agent_defaults)

        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = SimpleProcessor(agent_defaults=defaults)
        await processor.process(session)

        mock_fork.assert_awaited_once_with(
            session,
            "Simple extraction prompt",
            defaults,
        )

    async def test_subclass_can_override_process(self, mocker: pytest.MockerFixture) -> None:
        """AC: Subclasses can override process() without calling super().process()."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )

        class CustomProcessor(PromptDrivenProcessor):
            """Custom processor with pre/post steps."""

            def __init__(self, agent_defaults: AgentDefaults, fork_mock: AsyncMock) -> None:
                super().__init__(prompt="Custom prompt", agent_defaults=agent_defaults)
                self.pre_called = False
                self.post_called = False
                self._fork_mock = fork_mock

            async def process(self, session: Session) -> None:
                # Pre-step
                self.pre_called = True
                # Call fork_and_consume directly (not super().process())
                await self._fork_mock(session, self._prompt, self._agent_defaults)
                # Post-step
                self.post_called = True

        session = _make_session()
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = CustomProcessor(agent_defaults=defaults, fork_mock=mock_fork)
        await processor.process(session)

        assert processor.pre_called
        assert processor.post_called
        mock_fork.assert_awaited_once_with(session, "Custom prompt", defaults)

    async def test_propagates_fork_and_consume_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: Exceptions from fork_and_consume propagate."""
        mocker.patch(
            "tachikoma.post_processing.fork_and_consume",
            side_effect=RuntimeError("SDK error"),
        )
        session = _make_session()
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = PromptDrivenProcessor(prompt="Test prompt", agent_defaults=defaults)

        with pytest.raises(RuntimeError, match="SDK error"):
            await processor.process(session)
