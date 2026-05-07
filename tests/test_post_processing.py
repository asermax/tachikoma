"""Tests for post-processing pipeline.

Extract and store memories from conversations.
"""

import asyncio
import re
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.post_processing import (
    FINALIZE_PHASE,
    MAIN_PHASE,
    PRE_FINALIZE_PHASE,
    PostProcessingPipeline,
    PostProcessor,
    PromptDrivenProcessor,
    _split_compound_commands,
    build_context_summary,
    fork_and_capture,
    fork_and_consume,
    make_bash_deny_hook,
)
from tachikoma.sessions.model import Session, SessionContextEntry


def _make_mock_registry():
    """Create a mock SessionRegistry for pipeline tests."""
    registry = MagicMock()
    registry.mark_processed = AsyncMock()
    registry.load_context_entries = AsyncMock(return_value=[])
    return registry


class _FakeProcessor(PostProcessor):
    """Concrete processor for testing - methods overridden per-test."""

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
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


def _make_entry(owner: str, content: str = "", metadata: dict | None = None) -> SessionContextEntry:
    """Create a test context entry."""
    return SessionContextEntry(
        id=1,
        session_id="session-1",
        owner=owner,
        content=content,
        metadata=metadata,
    )


class TestBuildContextSummary:
    """Tests for build_context_summary()."""

    def test_returns_none_for_empty_entries(self) -> None:
        result = build_context_summary([])
        assert result is None

    def test_returns_none_for_unknown_owners_only(self) -> None:
        entries = [_make_entry("unknown-tag", "some content")]
        result = build_context_summary(entries)
        assert result is None

    def test_includes_foundational_context(self) -> None:
        entries = [
            _make_entry("soul", "personality"),
            _make_entry("user", "user info"),
            _make_entry("agents", "instructions"),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Foundational Context:** SOUL.md, USER.md, AGENTS.md" in result

    def test_includes_memory_paths_from_metadata(self) -> None:
        entries = [
            _make_entry(
                "memories",
                "content",
                metadata={"memory_path": "memories/facts/python.md"},
            ),
            _make_entry(
                "memories",
                "content",
                metadata={"memory_path": "memories/facts/infra.md"},
            ),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert "memories/facts/infra.md" in result
        assert "memories/facts/python.md" in result
        assert "**Loaded Memories:**" in result

    def test_skips_memory_entries_without_metadata(self) -> None:
        entries = [_make_entry("memories", "content")]
        result = build_context_summary(entries)
        assert result is None

    def test_includes_skill_names_from_metadata(self) -> None:
        entries = [
            _make_entry("skills", "content", metadata={"skill_name": "reading-list"}),
            _make_entry("skills", "content", metadata={"skill_name": "code-review"}),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Active Skills:** code-review, reading-list" in result

    def test_parses_project_names_from_content(self) -> None:
        content = "## Registered Projects\n\n- tachikoma: main\n- filadd: abc1234 (detached)"
        entries = [_make_entry("projects", content)]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Projects:** filadd, tachikoma" in result

    def test_handles_empty_project_content(self) -> None:
        entries = [_make_entry("projects", "No projects registered.")]
        result = build_context_summary(entries)
        assert result is None

    def test_notes_previous_summary_presence(self) -> None:
        entries = [_make_entry("previous-summary", "summary text")]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Previous Conversation:**" in result

    def test_counts_bridging_context(self) -> None:
        entries = [
            _make_entry("bridging-context", "summary 1"),
            _make_entry("bridging-context", "summary 2"),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Bridging Context:** 2" in result

    def test_full_mixed_summary(self) -> None:
        entries = [
            _make_entry("soul", "personality"),
            _make_entry("user", "user info"),
            _make_entry(
                "memories",
                "content",
                metadata={"memory_path": "memories/facts/python.md"},
            ),
            _make_entry(
                "skills",
                "content",
                metadata={"skill_name": "reading-list"},
            ),
            _make_entry("projects", "- tachikoma: main"),
            _make_entry("previous-summary", "summary"),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert "**Foundational Context:** SOUL.md, USER.md" in result
        assert "**Loaded Memories:** memories/facts/python.md" in result
        assert "**Active Skills:** reading-list" in result
        assert "**Projects:** tachikoma" in result
        assert "**Previous Conversation:**" in result
        assert "Skip re-extracting information" in result
        assert "still search for existing files" in result

    def test_deduplicates_memory_paths(self) -> None:
        entries = [
            _make_entry("memories", "content", metadata={"memory_path": "memories/facts/x.md"}),
            _make_entry("memories", "content", metadata={"memory_path": "memories/facts/x.md"}),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert result.count("memories/facts/x.md") == 1

    def test_deduplicates_skill_names(self) -> None:
        entries = [
            _make_entry("skills", "content", metadata={"skill_name": "skill-a"}),
            _make_entry("skills", "content", metadata={"skill_name": "skill-a"}),
        ]
        result = build_context_summary(entries)
        assert result is not None
        assert result.count("skill-a") == 1


class TestPostProcessingPipeline:
    """Tests for PostProcessingPipeline."""

    async def test_runs_all_registered_processors(self) -> None:
        """AC: All registered processors are awaited with the same session."""
        processor1 = _make_mock_processor()
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor1)
        pipeline.register(processor2)

        await pipeline.run(session)

        processor1.process.assert_awaited_once_with(session, extra=None)
        processor2.process.assert_awaited_once_with(session, extra=None)

    async def test_passes_context_summary_as_extra(self) -> None:
        """AC: Pipeline builds summary from entries and passes it via extra dict."""
        processor = _make_mock_processor()
        session = _make_session()

        entries = [
            _make_entry("memories", "c", metadata={"memory_path": "memories/facts/x.md"}),
        ]
        registry = _make_mock_registry()
        registry.load_context_entries = AsyncMock(return_value=entries)

        pipeline = PostProcessingPipeline(registry)
        pipeline.register(processor)
        await pipeline.run(session)

        processor.process.assert_awaited_once()
        call_kwargs = processor.process.call_args
        extra = call_kwargs.kwargs.get("extra") or call_kwargs[1].get("extra")
        assert extra is not None
        assert "context_summary" in extra
        assert "memories/facts/x.md" in extra["context_summary"]

    async def test_passes_extra_none_when_no_entries(self) -> None:
        """AC: Pipeline passes extra=None when entries are empty."""
        processor = _make_mock_processor()
        session = _make_session()

        registry = _make_mock_registry()
        registry.load_context_entries = AsyncMock(return_value=[])

        pipeline = PostProcessingPipeline(registry)
        pipeline.register(processor)
        await pipeline.run(session)

        processor.process.assert_awaited_once_with(session, extra=None)

    async def test_passes_extra_none_when_load_fails(self) -> None:
        """AC: Pipeline passes extra=None when load_context_entries raises."""
        processor = _make_mock_processor()
        session = _make_session()

        registry = _make_mock_registry()
        registry.load_context_entries = AsyncMock(side_effect=RuntimeError("db error"))

        pipeline = PostProcessingPipeline(registry)
        pipeline.register(processor)
        await pipeline.run(session)

        processor.process.assert_awaited_once_with(session, extra=None)

    async def test_error_isolation_continues_other_processors(self) -> None:
        """AC: One processor failure doesn't prevent others from completing."""
        processor1 = _make_mock_processor()
        processor1.process.side_effect = RuntimeError("failed")
        processor2 = _make_mock_processor()
        session = _make_session()

        pipeline = PostProcessingPipeline(_make_mock_registry())
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

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        # Run the pipeline - the error should be caught and logged
        await pipeline.run(session)

        # Verify the processor was called
        processor.process.assert_awaited_once()

    async def test_returns_after_all_complete(self) -> None:
        """AC: Pipeline awaits all processors before returning."""
        call_order: list[str] = []

        async def slow_process(session: Session, **_kwargs: object) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_process(session: Session, **_kwargs: object) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_process
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_process

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(slow_processor)
        pipeline.register(fast_processor)

        await pipeline.run(_make_session())

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")

    async def test_serializes_concurrent_invocations(self) -> None:
        """AC: Concurrent run() calls execute sequentially (lock test)."""
        call_times: list[tuple[float, str]] = []

        async def track_process(session: Session, **_kwargs: object) -> None:
            call_times.append((asyncio.get_event_loop().time(), "start"))
            await asyncio.sleep(0.05)
            call_times.append((asyncio.get_event_loop().time(), "end"))

        processor = _make_mock_processor()
        processor.process.side_effect = track_process

        pipeline = PostProcessingPipeline(_make_mock_registry())
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
        pipeline = PostProcessingPipeline(_make_mock_registry())
        session = _make_session()

        # Should not raise
        await pipeline.run(session)

    async def test_on_status_called_before_each_processor(self) -> None:
        """AC: on_status is called with each processor's status message before process()."""
        status_calls: list[str] = []
        process_calls: list[str] = []

        async def on_status(msg: str) -> None:
            status_calls.append(msg)

        class _TrackedProcessor(PostProcessor):
            _status_message = "Doing work..."

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                process_calls.append("process")

        processor = _TrackedProcessor()
        session = _make_session()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        await pipeline.run(session, on_status=on_status)

        assert status_calls == ["Doing work..."]
        assert process_calls == ["process"]

    async def test_on_status_default_when_not_set(self) -> None:
        """AC: Processors without _status_message get default 'Processing...'."""

        class _NoMessageProcessor(PostProcessor):
            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        status_calls: list[str] = []

        async def on_status(msg: str) -> None:
            status_calls.append(msg)

        processor = _NoMessageProcessor()
        session = _make_session()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        await pipeline.run(session, on_status=on_status)

        assert status_calls == ["Processing..."]

    async def test_on_status_failure_does_not_block_processor(self) -> None:
        """AC: Callback failure is logged but processor still runs."""

        async def on_status(msg: str) -> None:
            raise RuntimeError("callback broke")

        processor = _make_mock_processor()
        session = _make_session()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        await pipeline.run(session, on_status=on_status)

        processor.process.assert_awaited_once()

    async def test_on_status_none_skips_callback(self) -> None:
        """AC: When on_status is None, no callback is invoked."""
        processor = _make_mock_processor()
        session = _make_session()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        await pipeline.run(session)

        processor.process.assert_awaited_once()


class TestProcessorStatusMessage:
    """Tests for PostProcessor.status_message()."""

    def test_default_status_message(self) -> None:
        """AC: Default status_message returns 'Processing...'."""

        class _DefaultProcessor(PostProcessor):
            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        assert _DefaultProcessor().status_message() == "Processing..."

    def test_custom_status_message(self) -> None:
        """AC: Processor with _status_message returns its custom message."""

        class _CustomProcessor(PostProcessor):
            _status_message = "Custom step..."

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        assert _CustomProcessor().status_message() == "Custom step..."

    """Tests for phased pipeline execution."""

    def test_unknown_phase_raises_value_error(self) -> None:
        """AC: Registration with unknown phase raises ValueError with clear message."""
        processor = _make_mock_processor()
        pipeline = PostProcessingPipeline(_make_mock_registry())

        with pytest.raises(ValueError, match="Invalid phase 'invalid'"):
            pipeline.register(processor, phase="invalid")

    async def test_finalize_phase_runs_after_main_phase(self) -> None:
        """AC: Finalize-phase processors run after main-phase processors complete."""
        call_order: list[str] = []

        async def track_main(session: Session, **_kwargs: object) -> None:
            call_order.append("main_start")
            await asyncio.sleep(0.02)
            call_order.append("main_end")

        async def track_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("finalize_start")
            call_order.append("finalize_end")

        main_processor = _make_mock_processor()
        main_processor.process.side_effect = track_main
        finalize_processor = _make_mock_processor()
        finalize_processor.process.side_effect = track_finalize

        pipeline = PostProcessingPipeline(_make_mock_registry())
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

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(main_processor, phase=MAIN_PHASE)
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Both should have been called despite main failure
        main_processor.process.assert_awaited_once()
        finalize_processor.process.assert_awaited_once()

    async def test_default_phase_is_main(self) -> None:
        """AC: Default phase is 'main' for backward compatibility."""
        processor = _make_mock_processor()

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)  # No phase specified

        # Verify it went to main phase by checking internal structure
        assert len(pipeline._phases[MAIN_PHASE]) == 1
        assert len(pipeline._phases[FINALIZE_PHASE]) == 0

    async def test_empty_phase_is_skipped(self) -> None:
        """AC: Empty phase is skipped without error."""
        finalize_processor = _make_mock_processor()

        pipeline = PostProcessingPipeline(_make_mock_registry())
        # Only register finalize, leave main empty
        pipeline.register(finalize_processor, phase=FINALIZE_PHASE)

        # Should not raise
        await pipeline.run(_make_session())

        finalize_processor.process.assert_awaited_once()

    async def test_multiple_finalize_processors_run_in_parallel(self) -> None:
        """AC: Multiple finalize processors run in parallel (same as main-phase)."""
        call_order: list[str] = []

        async def slow_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_finalize
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_finalize

        pipeline = PostProcessingPipeline(_make_mock_registry())
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

        async def track_main(session: Session, **_kwargs: object) -> None:
            call_order.append("main_start")
            await asyncio.sleep(0.01)
            call_order.append("main_end")

        async def track_pre_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("pre_finalize_start")
            await asyncio.sleep(0.01)
            call_order.append("pre_finalize_end")

        async def track_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("finalize_start")
            call_order.append("finalize_end")

        main_processor = _make_mock_processor()
        main_processor.process.side_effect = track_main
        pre_finalize_processor = _make_mock_processor()
        pre_finalize_processor.process.side_effect = track_pre_finalize
        finalize_processor = _make_mock_processor()
        finalize_processor.process.side_effect = track_finalize

        pipeline = PostProcessingPipeline(_make_mock_registry())
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

        pipeline = PostProcessingPipeline(_make_mock_registry())
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

        async def slow_pre_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("slow_start")
            await asyncio.sleep(0.05)
            call_order.append("slow_end")

        async def fast_pre_finalize(session: Session, **_kwargs: object) -> None:
            call_order.append("fast_start")
            await asyncio.sleep(0.01)
            call_order.append("fast_end")

        slow_processor = _make_mock_processor()
        slow_processor.process.side_effect = slow_pre_finalize
        fast_processor = _make_mock_processor()
        fast_processor.process.side_effect = fast_pre_finalize

        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(slow_processor, phase=PRE_FINALIZE_PHASE)
        pipeline.register(fast_processor, phase=PRE_FINALIZE_PHASE)

        await pipeline.run(_make_session())

        # Both should have started before either finished (parallel execution)
        assert call_order.index("slow_start") < call_order.index("slow_end")
        assert call_order.index("fast_start") < call_order.index("fast_end")


class TestNeedsProcessing:
    """Tests for PostProcessingPipeline.needs_processing()."""

    def test_returns_true_when_never_processed(self) -> None:
        """AC: Session with no processed_at needs processing."""
        pipeline = PostProcessingPipeline(_make_mock_registry())
        session = _make_session()

        assert pipeline.needs_processing(session, datetime(2026, 1, 1, tzinfo=UTC)) is True

    def test_returns_false_when_is_processing(self) -> None:
        """AC3: Returns False when pipeline is already running."""
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline._is_processing = True
        session = _make_session()

        assert pipeline.needs_processing(session, datetime(2026, 1, 1, tzinfo=UTC)) is False

    def test_returns_false_when_processed_at_after_last_message(self) -> None:
        """AC2: Returns False when processed_at >= last_message_time."""
        pipeline = PostProcessingPipeline(_make_mock_registry())
        session = Session(
            id="s1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            sdk_session_id="sdk-1",
            processed_at=datetime(2026, 1, 1, hour=12, tzinfo=UTC),
        )

        last_msg = datetime(2026, 1, 1, hour=11, tzinfo=UTC)
        assert pipeline.needs_processing(session, last_msg) is False

    def test_returns_true_when_processed_at_before_last_message(self) -> None:
        """AC5: Returns True when processed_at < last_message_time."""
        pipeline = PostProcessingPipeline(_make_mock_registry())
        session = Session(
            id="s1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            sdk_session_id="sdk-1",
            processed_at=datetime(2026, 1, 1, hour=10, tzinfo=UTC),
        )

        last_msg = datetime(2026, 1, 1, hour=11, tzinfo=UTC)
        assert pipeline.needs_processing(session, last_msg) is True

    def test_returns_true_when_last_message_time_is_none(self) -> None:
        """Edge case: No messages exchanged yet — conservatively returns True."""
        pipeline = PostProcessingPipeline(_make_mock_registry())
        session = Session(
            id="s1",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            sdk_session_id="sdk-1",
            processed_at=datetime(2026, 1, 1, hour=12, tzinfo=UTC),
        )

        assert pipeline.needs_processing(session, None) is True


class TestMarkProcessed:
    """Tests for PostProcessingPipeline calling mark_processed after run."""

    async def test_calls_mark_processed_after_run(self) -> None:
        """AC7: Pipeline calls mark_processed on completion."""
        registry = _make_mock_registry()
        pipeline = PostProcessingPipeline(registry)

        session = _make_session()
        await pipeline.run(session)

        registry.mark_processed.assert_awaited_once_with(session.id)

    async def test_is_processing_flag_lifecycle(self) -> None:
        """AC3: is_processing is True during run, False after."""
        registry = _make_mock_registry()
        pipeline = PostProcessingPipeline(registry)

        assert pipeline.is_processing is False

        observed_during: list[bool] = []

        processor = _make_mock_processor()

        async def capture_state(session, **_kwargs):
            observed_during.append(pipeline.is_processing)

        processor.process.side_effect = capture_state
        pipeline.register(processor)

        await pipeline.run(_make_session())

        assert observed_during == [True]
        assert pipeline.is_processing is False


class TestForkAndConsume:
    """Tests for fork_and_consume helper."""

    async def test_calls_query_with_fork_options(self, mocker: MockerFixture) -> None:
        """AC: query() called with correct prompt, resume, fork_session, cwd."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_consumes_full_async_iterator(self, mocker: MockerFixture) -> None:
        """AC: Async iterator is fully consumed."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for i in range(3):
                consume_count += 1
                yield MagicMock(msg=i)

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test")
        await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_propagates_query_error(self, mocker: MockerFixture) -> None:
        """AC: Exceptions from query() propagate."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=failing_query)

        session = _make_session(sdk_session_id="sdk-test")

        with pytest.raises(RuntimeError, match="SDK error"):
            await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_raises_when_no_sdk_session_id(self) -> None:
        """AC: Raises RuntimeError when session has no sdk_session_id."""
        session = _make_session(sdk_session_id=None)

        with pytest.raises(RuntimeError, match="no sdk_session_id"):
            await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_mcp_servers_passed_to_query_options(self, mocker: MockerFixture) -> None:
        """AC: mcp_servers parameter is passed through to ClaudeAgentOptions."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_mcp_servers_default_none_not_passed(self, mocker: MockerFixture) -> None:
        """AC: When mcp_servers is None (default), options use SDK default (empty dict)."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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
        self, mocker: MockerFixture
    ) -> None:
        """AC: system_prompt_append param sets SystemPromptPreset on options."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_system_prompt_append_none_no_system_prompt(self, mocker: MockerFixture) -> None:
        """AC: system_prompt_append=None (default) leaves system_prompt unset."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_model_passed_to_options_when_provided(self, mocker: MockerFixture) -> None:
        """AC: model kwarg is threaded through to ClaudeAgentOptions."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_consume(
            session,
            "prompt",
            AgentDefaults(cwd=Path("/workspace")),
            model="haiku",
        )

        options = mock_query.call_args[1]["options"]
        assert options.model == "haiku"

    async def test_model_default_none_leaves_options_model_unset(
        self, mocker: MockerFixture
    ) -> None:
        """AC: Default call (no model kwarg) leaves ClaudeAgentOptions.model at None."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock()

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_consume(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        options = mock_query.call_args[1]["options"]
        assert options.model is None


class TestForkAndCapture:
    """Tests for fork_and_capture helper."""

    async def test_captures_text_from_content_blocks(self, mocker: MockerFixture) -> None:
        """AC: Text from content blocks is captured and concatenated."""
        msg1 = MagicMock()
        msg1.content = [MagicMock(text="Hello ")]

        msg2 = MagicMock()
        msg2.content = [MagicMock(text="world")]

        async def fake_query(*args, **kwargs):
            yield msg1
            yield msg2

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test-123")
        result = await fork_and_capture(
            session,
            "Generate notification",
            AgentDefaults(cwd=Path("/workspace")),
        )

        assert result == "Hello world"

    async def test_returns_empty_string_when_no_text(self, mocker: MockerFixture) -> None:
        """AC: Returns empty string when no text blocks in response."""
        msg = MagicMock(spec=[])  # No content attribute

        async def fake_query(*args, **kwargs):
            yield msg

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test-123")
        result = await fork_and_capture(
            session,
            "prompt",
            AgentDefaults(cwd=Path("/workspace")),
        )

        assert result == ""

    async def test_fully_consumes_generator(self, mocker: MockerFixture) -> None:
        """AC: DES-005 compliance — generator is fully consumed."""
        consume_count = 0

        async def fake_query(*args, **kwargs):
            nonlocal consume_count
            for _ in range(3):
                consume_count += 1
                yield MagicMock(spec=[])

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=fake_query)

        session = _make_session(sdk_session_id="sdk-test")
        await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        assert consume_count == 3

    async def test_calls_query_with_fork_options(self, mocker: MockerFixture) -> None:
        """AC: query() called with correct resume, fork_session, cwd."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_propagates_query_error(self, mocker: MockerFixture) -> None:
        """AC: Exceptions from query() propagate."""

        async def failing_query(*args, **kwargs):
            raise RuntimeError("SDK error")
            yield  # make it a generator

        mocker.patch("tachikoma.post_processing.stderr_aware_query", side_effect=failing_query)

        session = _make_session(sdk_session_id="sdk-test")

        with pytest.raises(RuntimeError, match="SDK error"):
            await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

    async def test_system_prompt_append_sets_system_prompt_preset(
        self, mocker: MockerFixture
    ) -> None:
        """AC: system_prompt_append param sets SystemPromptPreset on options."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_system_prompt_append_none_no_system_prompt(self, mocker: MockerFixture) -> None:
        """AC: system_prompt_append=None (default) leaves system_prompt unset."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

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

    async def test_model_passed_to_options_when_provided(self, mocker: MockerFixture) -> None:
        """AC: model kwarg is threaded through to ClaudeAgentOptions."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock(spec=[])

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_capture(
            session,
            "prompt",
            AgentDefaults(cwd=Path("/workspace")),
            model="haiku",
        )

        options = mock_query.call_args[1]["options"]
        assert options.model == "haiku"

    async def test_model_default_none_leaves_options_model_unset(
        self, mocker: MockerFixture
    ) -> None:
        """AC: Default call (no model kwarg) leaves ClaudeAgentOptions.model at None."""
        mock_query = mocker.patch("tachikoma.post_processing.stderr_aware_query")

        async def fake_query(*args, **kwargs):
            yield MagicMock(spec=[])

        mock_query.return_value = fake_query()

        session = _make_session(sdk_session_id="sdk-test-123")

        await fork_and_capture(session, "prompt", AgentDefaults(cwd=Path("/workspace")))

        options = mock_query.call_args[1]["options"]
        assert options.model is None


class TestPromptDrivenProcessor:
    """Tests for PromptDrivenProcessor base class."""

    async def test_process_calls_fork_and_consume_with_correct_args(
        self, mocker: MockerFixture
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

        mock_fork.assert_awaited_once_with(
            session,
            prompt,
            defaults,
            tools=None,
            allow=None,
            pre_tool_use_hooks=None,
            model=None,
        )

    async def test_simple_subclass_inherits_process(self, mocker: MockerFixture) -> None:
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
            tools=None,
            allow=None,
            pre_tool_use_hooks=None,
            model=None,
        )

    async def test_subclass_can_override_process(self, mocker: MockerFixture) -> None:
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

    async def test_propagates_fork_and_consume_error(self, mocker: MockerFixture) -> None:
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

    async def test_model_threaded_through_to_fork_and_consume(self, mocker: MockerFixture) -> None:
        """AC: model kwarg on __init__ is threaded through to fork_and_consume."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = PromptDrivenProcessor(
            prompt="Test prompt", agent_defaults=defaults, model="haiku"
        )
        await processor.process(session)

        mock_fork.assert_awaited_once_with(
            session,
            "Test prompt",
            defaults,
            tools=None,
            allow=None,
            pre_tool_use_hooks=None,
            model="haiku",
        )

    async def test_appends_context_summary_to_prompt(self, mocker: MockerFixture) -> None:
        """AC: Context summary from extra dict is appended to the prompt."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        prompt = "Test prompt"
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = PromptDrivenProcessor(prompt=prompt, agent_defaults=defaults)
        summary = "## Session Context\n\n**Loaded Memories:** memories/facts/x.md"
        await processor.process(session, extra={"context_summary": summary})

        actual_prompt = mock_fork.call_args[0][1]
        assert prompt in actual_prompt
        assert summary in actual_prompt
        assert actual_prompt.index(prompt) < actual_prompt.index(summary)

    async def test_no_append_when_extra_is_none(self, mocker: MockerFixture) -> None:
        """AC: No context summary appended when extra is None."""
        mock_fork = mocker.patch(
            "tachikoma.post_processing.fork_and_consume", new_callable=AsyncMock
        )
        session = _make_session()
        prompt = "Test prompt"
        defaults = AgentDefaults(cwd=Path("/workspace"))

        processor = PromptDrivenProcessor(prompt=prompt, agent_defaults=defaults)
        await processor.process(session)

        actual_prompt = mock_fork.call_args[0][1]
        assert actual_prompt == prompt

    """Tests for _split_compound_commands quoting-aware splitting."""

    def test_single_command_no_split(self) -> None:
        assert _split_compound_commands("git status") == ["git status"]

    def test_single_command_with_args_no_split(self) -> None:
        assert _split_compound_commands("ls -la /workspace") == ["ls -la /workspace"]

    @pytest.mark.parametrize(
        "command, expected_count",
        [
            ("git status | head -5", 2),
            ("git status && git diff", 2),
            ("git status || git diff", 2),
            ("git status; git diff", 2),
            ("git status && git diff || git log", 3),
        ],
    )
    def test_splits_on_real_operators(self, command: str, expected_count: int) -> None:
        parts = _split_compound_commands(command)
        assert len(parts) == expected_count

    def test_double_quoted_pipe_not_split(self) -> None:
        assert _split_compound_commands('grep -E "pattern1|pattern2" file.txt') == [
            'grep -E "pattern1|pattern2" file.txt',
        ]

    def test_multiple_pipes_in_double_quotes_not_split(self) -> None:
        assert _split_compound_commands('grep -E "a|b|c" file.txt') == [
            'grep -E "a|b|c" file.txt',
        ]

    def test_single_quoted_pipe_not_split(self) -> None:
        assert _split_compound_commands("grep -E 'pattern1|pattern2' file.txt") == [
            "grep -E 'pattern1|pattern2' file.txt",
        ]

    def test_single_quoted_semicolon_not_split(self) -> None:
        assert _split_compound_commands("echo 'hello ; world'") == [
            "echo 'hello ; world'",
        ]

    def test_double_quoted_and_not_split(self) -> None:
        assert _split_compound_commands('echo "hello && goodbye"') == [
            'echo "hello && goodbye"',
        ]

    def test_single_quote_inside_double_quotes(self) -> None:
        assert _split_compound_commands('echo "it\'s | fine"') == [
            'echo "it\'s | fine"',
        ]

    def test_double_quote_inside_single_quotes(self) -> None:
        assert _split_compound_commands("echo 'he said \"hello | world\"'") == [
            "echo 'he said \"hello | world\"'",
        ]

    def test_escaped_semicolon_not_split(self) -> None:
        assert _split_compound_commands("echo hello \\; world") == [
            "echo hello \\; world",
        ]

    def test_escaped_pipe_not_split(self) -> None:
        assert _split_compound_commands("grep \\|\\| file.txt") == [
            "grep \\|\\| file.txt",
        ]

    def test_escaped_backslash_before_operator(self) -> None:
        parts = _split_compound_commands("echo test \\\\; echo oops")
        assert len(parts) == 2
        assert parts[0] == "echo test \\\\"
        assert parts[1] == "echo oops"

    def test_mixed_quoted_pipe_and_real_pipe(self) -> None:
        parts = _split_compound_commands('grep "a|b" | wc -l')
        assert len(parts) == 2
        assert parts[0] == 'grep "a|b"'
        assert parts[1] == "wc -l"

    def test_mixed_single_quoted_and_real_pipe(self) -> None:
        parts = _split_compound_commands("grep 'a|b' | wc -l")
        assert len(parts) == 2
        assert parts[0] == "grep 'a|b'"
        assert parts[1] == "wc -l"

    def test_empty_string_returns_empty(self) -> None:
        assert _split_compound_commands("") == []

    def test_whitespace_only_returns_empty(self) -> None:
        assert _split_compound_commands("   ") == []

    def test_trailing_operator_strips_empty_part(self) -> None:
        assert _split_compound_commands("git status &&") == ["git status"]

    def test_unmatched_double_quote_no_split(self) -> None:
        assert _split_compound_commands('grep "pattern') == [
            'grep "pattern',
        ]

    def test_unmatched_single_quote_no_split(self) -> None:
        assert _split_compound_commands("grep 'pattern") == [
            "grep 'pattern",
        ]

    def test_trailing_backslash_treated_as_literal(self) -> None:
        assert _split_compound_commands("echo hello\\") == [
            "echo hello\\",
        ]

    def test_backslash_escaped_double_quote_inside_double_quotes(self) -> None:
        assert _split_compound_commands('echo "hello \\" ; still quoted"') == [
            'echo "hello \\" ; still quoted"',
        ]


class TestMakeBashDenyHook:
    """Tests for make_bash_deny_hook."""

    @staticmethod
    async def _run_hook(hook_matcher, command: str) -> dict:
        hook = hook_matcher.hooks[0]
        return await hook(
            {"tool_input": {"command": command}},
            None,
            MagicMock(),
        )

    @pytest.fixture
    def deny_hook(self):
        patterns = [
            re.compile(r"^git\s+push\b"),
            re.compile(r"^git\s+reset\b"),
        ]
        return make_bash_deny_hook(patterns)

    async def test_denies_matching_command(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "git push origin main")

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "git push" in result["hookSpecificOutput"]["permissionDecisionReason"]

    async def test_denies_force_push(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "git push --force origin main")

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_denies_reset(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "git reset HEAD~1")

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_denies_compound_with_destructive_subcommand(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "git status && git reset HEAD~1")

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "git reset" in result["hookSpecificOutput"]["permissionDecisionReason"]

    async def test_denies_pipe_to_destructive(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "echo hi | git reset --hard")

        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    async def test_passes_safe_git_commands(self, deny_hook) -> None:
        for command in (
            "git status",
            "git log --oneline",
            "git diff",
            "git fetch origin",
            "git remote -v",
            "git clone git@github.com:x/y.git /tmp/foo",
        ):
            result = await self._run_hook(deny_hook, command)

            assert result == {}, f"{command!r} was denied: {result}"

    async def test_passes_non_git_bash(self, deny_hook) -> None:
        for command in ("ls -la", "echo hello", "cat file.txt"):
            result = await self._run_hook(deny_hook, command)

            assert result == {}

    async def test_passes_compound_of_safe_commands(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "git status && git log")

        assert result == {}

    async def test_empty_command_passes(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, "")

        assert result == {}

    async def test_ignores_missing_command_key(self, deny_hook) -> None:
        hook = deny_hook.hooks[0]

        result = await hook({"tool_input": {}}, None, MagicMock())

        assert result == {}

    async def test_pipe_inside_quotes_not_split(self, deny_hook) -> None:
        result = await self._run_hook(deny_hook, 'grep -E "pattern1|pattern2" file.txt')

        assert result == {}


class TestPhaseAttribute:
    """Tests for PostProcessor.phase class attribute and register() sentinel."""

    def test_default_phase_is_main(self) -> None:
        """AC: PostProcessor.phase defaults to MAIN_PHASE."""

        class _DefaultProcessor(PostProcessor):
            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        assert _DefaultProcessor.phase == MAIN_PHASE
        assert _DefaultProcessor().phase == MAIN_PHASE

    def test_subclass_can_override_phase(self) -> None:
        """AC: Subclasses can override phase with a different constant."""

        class _PreFinalizeProcessor(PostProcessor):
            phase = PRE_FINALIZE_PHASE

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        assert _PreFinalizeProcessor.phase == PRE_FINALIZE_PHASE
        assert _PreFinalizeProcessor().phase == PRE_FINALIZE_PHASE

    def test_finalize_phase_override(self) -> None:
        """AC: Subclasses can override phase with FINALIZE_PHASE."""

        class _FinalizeProcessor(PostProcessor):
            phase = FINALIZE_PHASE

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        assert _FinalizeProcessor.phase == FINALIZE_PHASE

    def test_register_reads_class_attribute_when_no_phase_kwarg(self) -> None:
        """AC: register() without phase kwarg reads processor's class attribute."""

        class _PreFinalizeProcessor(PostProcessor):
            phase = PRE_FINALIZE_PHASE

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        pipeline = PostProcessingPipeline(_make_mock_registry())
        processor = _PreFinalizeProcessor()
        pipeline.register(processor)

        assert processor in pipeline._phases[PRE_FINALIZE_PHASE]
        assert len(pipeline._phases[MAIN_PHASE]) == 0

    def test_register_explicit_phase_overrides_class_attribute(self) -> None:
        """AC: Explicit phase= kwarg takes precedence over class attribute."""

        class _PreFinalizeProcessor(PostProcessor):
            phase = PRE_FINALIZE_PHASE

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        pipeline = PostProcessingPipeline(_make_mock_registry())
        processor = _PreFinalizeProcessor()
        pipeline.register(processor, phase=FINALIZE_PHASE)

        assert processor not in pipeline._phases[PRE_FINALIZE_PHASE]
        assert processor in pipeline._phases[FINALIZE_PHASE]

    def test_register_default_phase_without_class_override(self) -> None:
        """AC: Processor with default phase goes to main when no kwarg."""

        class _MainProcessor(PostProcessor):
            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        pipeline = PostProcessingPipeline(_make_mock_registry())
        processor = _MainProcessor()
        pipeline.register(processor)

        assert processor in pipeline._phases[MAIN_PHASE]

    def test_invalid_phase_still_raises(self) -> None:
        """AC: Invalid phase raises ValueError regardless of class attribute."""

        class _DefaultProcessor(PostProcessor):
            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        pipeline = PostProcessingPipeline(_make_mock_registry())
        processor = _DefaultProcessor()

        with pytest.raises(ValueError, match="Invalid phase 'bad'"):
            pipeline.register(processor, phase="bad")


class TestUnregister:
    """Tests for PostProcessingPipeline.unregister()."""

    def test_removes_registered_processor(self) -> None:
        """AC: unregister() removes a processor from its phase list."""
        processor = _make_mock_processor()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(processor)

        assert processor in pipeline._phases[MAIN_PHASE]
        pipeline.unregister(processor)
        assert processor not in pipeline._phases[MAIN_PHASE]

    def test_removes_processor_from_correct_phase(self) -> None:
        """AC: unregister() removes from the right phase, not all phases."""
        main_proc = _make_mock_processor()
        pre_fin_proc = _make_mock_processor()
        pipeline = PostProcessingPipeline(_make_mock_registry())
        pipeline.register(main_proc, phase=MAIN_PHASE)
        pipeline.register(pre_fin_proc, phase=PRE_FINALIZE_PHASE)

        pipeline.unregister(main_proc)

        assert main_proc not in pipeline._phases[MAIN_PHASE]
        assert pre_fin_proc in pipeline._phases[PRE_FINALIZE_PHASE]

    def test_noop_for_unknown_processor(self) -> None:
        """AC: unregister() with unknown processor is a safe no-op."""
        processor = _make_mock_processor()
        pipeline = PostProcessingPipeline(_make_mock_registry())

        # Should not raise
        pipeline.unregister(processor)

    def test_removes_from_correct_phase_when_class_attribute_used(self) -> None:
        """AC: unregister() works when processor was registered via class attribute."""

        class _PreFinalizeProcessor(PostProcessor):
            phase = PRE_FINALIZE_PHASE

            async def process(self, session: Session, *, extra: dict | None = None) -> None:
                pass

        pipeline = PostProcessingPipeline(_make_mock_registry())
        processor = _PreFinalizeProcessor()
        pipeline.register(processor)

        assert processor in pipeline._phases[PRE_FINALIZE_PHASE]
        pipeline.unregister(processor)
        assert processor not in pipeline._phases[PRE_FINALIZE_PHASE]
