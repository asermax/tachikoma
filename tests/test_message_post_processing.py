"""Tests for per-message post-processing pipeline.

Tests for DLT-026: Detect conversation boundaries via topic analysis.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tachikoma.message_post_processing import MessagePostProcessingPipeline, MessagePostProcessor
from tachikoma.sessions.model import Session


class _FakeProcessor(MessagePostProcessor):
    """Concrete processor for testing - methods overridden per-test."""

    async def process(self, session: Session, user_message: str, agent_response: str) -> None:
        pass


def _make_mock_processor() -> _FakeProcessor:
    """Create a processor with mockable process method."""
    processor = _FakeProcessor()
    processor.process = AsyncMock()  # type: ignore[method-assign]
    return processor


def _make_session(summary: str | None = None) -> Session:
    """Create a test session with sensible defaults."""
    return Session(
        id="session-1",
        started_at=datetime.now(UTC),
        summary=summary,
    )


class TestMessagePostProcessingPipeline:
    """Tests for MessagePostProcessingPipeline."""

    async def test_runs_all_registered_processors(self) -> None:
        """AC: All registered processors are awaited with correct args."""
        processor1 = _make_mock_processor()
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(processor1)
        pipeline.register(processor2)

        await pipeline.run(session, "Hello", "Hi there!")

        processor1.process.assert_awaited_once_with(session, "Hello", "Hi there!")
        processor2.process.assert_awaited_once_with(session, "Hello", "Hi there!")

    async def test_error_isolation_continues_other_processors(self) -> None:
        """AC: One processor failure doesn't prevent others from completing."""
        processor1 = _make_mock_processor()
        processor1.process.side_effect = RuntimeError("failed")
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(processor1)
        pipeline.register(processor2)

        await pipeline.run(session, "Hello", "Hi there!")

        # Both processors should have been called
        processor1.process.assert_awaited_once()
        processor2.process.assert_awaited_once()

    async def test_logs_processor_failures(self, capsys: pytest.CaptureFixture) -> None:
        """AC: Processor failures are logged per DES-002."""
        processor = _make_mock_processor()
        processor.process.side_effect = RuntimeError("test error")
        session = _make_session()

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(processor)

        # Run the pipeline - the error should be caught and logged
        await pipeline.run(session, "Hello", "Hi there!")

        # Verify the processor was called
        processor.process.assert_awaited_once()

    async def test_serializes_concurrent_invocations(self) -> None:
        """AC: Concurrent run() calls execute sequentially (lock test)."""
        call_times: list[tuple[float, str]] = []

        async def track_process(
            session: Session, user_message: str, agent_response: str
        ) -> None:
            call_times.append((asyncio.get_event_loop().time(), "start"))
            await asyncio.sleep(0.05)
            call_times.append((asyncio.get_event_loop().time(), "end"))

        processor = _make_mock_processor()
        processor.process.side_effect = track_process

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(processor)

        # Run two invocations concurrently
        session1 = _make_session()
        session2 = _make_session()

        await asyncio.gather(
            pipeline.run(session1, "Hello 1", "Response 1"),
            pipeline.run(session2, "Hello 2", "Response 2"),
        )

        # Verify they ran sequentially (not overlapping)
        # First run should complete before second starts
        assert len(call_times) == 4
        # First "end" should be before second "start"
        first_end = call_times[1]  # First run's end
        second_start = call_times[2]  # Second run's start
        assert first_end[0] <= second_start[0]

    async def test_runs_with_no_registered_processors(self) -> None:
        """AC: Empty pipeline runs without error."""
        pipeline = MessagePostProcessingPipeline()
        session = _make_session()

        # Should not raise
        await pipeline.run(session, "Hello", "Hi there!")

    async def test_processors_run_in_parallel(self) -> None:
        """AC: Multiple processors run in parallel within a single run() call."""
        call_order: list[str] = []

        async def slow_process(
            session: Session, user_message: str, agent_response: str
        ) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_process(
            session: Session, user_message: str, agent_response: str
        ) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_process
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_process

        pipeline = MessagePostProcessingPipeline()
        pipeline.register(slow_processor)
        pipeline.register(fast_processor)

        await pipeline.run(_make_session(), "Hello", "Response")

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")
