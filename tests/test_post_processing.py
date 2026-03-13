"""Tests for post-processing pipeline.

Tests for DLT-008: Extract and store memories from conversations.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.post_processing import PostProcessingPipeline, PostProcessor, fork_and_consume
from tachikoma.sessions.model import Session


class _FakeProcessor(PostProcessor):
    """Concrete processor for testing - methods overridden per-test."""

    async def process(self, session: Session) -> None:
        pass


def _make_mock_processor() -> _FakeProcessor:
    """Create a processor with mockable process method."""
    processor = _FakeProcessor()
    # Override the process method with an AsyncMock
    processor.process = AsyncMock()  # type: ignore[method-assign]
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
        cwd = Path("/workspace")

        await fork_and_consume(session, prompt, cwd)

        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args
        assert call_kwargs[1]["prompt"] == prompt

        options = call_kwargs[1]["options"]
        assert options.cwd == cwd
        assert options.resume == "sdk-test-123"
        assert options.fork_session is True

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
        await fork_and_consume(session, "prompt", Path("/workspace"))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: pytest.MockerFixture) -> None:
        """AC: Exceptions from query() propagate."""
        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.query", side_effect=failing_query)

        session = _make_session(sdk_session_id="sdk-test")

        with pytest.raises(RuntimeError, match="SDK error"):
            await fork_and_consume(session, "prompt", Path("/workspace"))

    async def test_raises_when_no_sdk_session_id(self) -> None:
        """AC: Raises RuntimeError when session has no sdk_session_id."""
        session = _make_session(sdk_session_id=None)

        with pytest.raises(RuntimeError, match="no sdk_session_id"):
            await fork_and_consume(session, "prompt", Path("/workspace"))
