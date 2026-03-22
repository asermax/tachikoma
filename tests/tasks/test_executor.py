"""Tests for background task executor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus

from tachikoma.config import TaskSettings
from tachikoma.tasks.events import TaskNotification
from tachikoma.tasks.executor import BackgroundTaskExecutor, background_task_runner
from tachikoma.tasks.repository import TaskRepository

from .conftest import _make_definition, _make_instance


class TestBackgroundTaskRunner:
    """Tests for the background_task_runner async function."""

    @pytest.mark.asyncio
    async def test_picks_up_pending_instances(self, repo: TaskRepository) -> None:
        """AC: Runner picks up pending background instances."""
        # Create pending background instance
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(max_concurrent_background=1)
        bus = EventBus()

        # Mock the executor to track calls
        executed_instances = []

        async def mock_execute(self, inst):
            executed_instances.append(inst.id)
            await repo.update_instance(inst.id, status="completed")

        with patch.object(BackgroundTaskExecutor, "execute", mock_execute):
            task = asyncio.create_task(
                background_task_runner(repo, settings, bus, cwd="/tmp")
            )
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert "inst-1" in executed_instances

    @pytest.mark.asyncio
    async def test_respects_concurrency_limit(self, repo: TaskRepository) -> None:
        """AC: Runner respects max_concurrent_background limit."""
        # Create multiple pending instances
        for i in range(5):
            instance = _make_instance(
                f"inst-{i}",
                task_type="background",
                status="pending",
            )
            await repo.create_instance(instance)

        settings = TaskSettings(max_concurrent_background=2)
        bus = EventBus()

        # Track concurrent executions
        concurrent_count = 0
        max_concurrent = 0

        async def mock_execute(self, inst):
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.1)
            await repo.update_instance(inst.id, status="completed")
            concurrent_count -= 1

        with patch.object(BackgroundTaskExecutor, "execute", mock_execute):
            task = asyncio.create_task(
                background_task_runner(repo, settings, bus, cwd="/tmp")
            )
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_skips_when_no_pending_instances(self, repo: TaskRepository) -> None:
        """AC: Runner handles empty queue gracefully."""
        settings = TaskSettings()
        bus = EventBus()

        execute_called = []

        async def mock_execute(self, inst):
            execute_called.append(inst.id)

        with patch.object(BackgroundTaskExecutor, "execute", mock_execute):
            task = asyncio.create_task(
                background_task_runner(repo, settings, bus, cwd="/tmp")
            )
            await asyncio.sleep(0.2)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(execute_called) == 0


class TestBackgroundTaskExecutor:
    """Tests for the BackgroundTaskExecutor class."""

    @pytest.mark.asyncio
    async def test_complete_flow_marks_completed(self, repo: TaskRepository) -> None:
        """AC: Executor marks instance completed when evaluator returns complete."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings()
        bus = EventBus()

        # Track dispatched events
        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            cwd="/tmp",
        )

        # Mock SDK client and evaluator
        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Mock query and receive_response
            mock_client.query = AsyncMock()

            # First response with session_id
            mock_result = MagicMock()
            mock_result.session_id = "sdk-session-123"
            mock_result.content = [MagicMock(text="Task done")]

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive

            # Mock evaluator to return complete
            with patch("claude_agent_sdk.query") as mock_query:
                eval_result = MagicMock()
                eval_result.content = [MagicMock(text='{"status": "complete", "feedback": "Done"}')]

                async def mock_eval_query(*args, **kwargs):
                    yield eval_result

                mock_query.return_value = mock_eval_query()

                # Mock pre-processing to return original prompt
                with patch.object(
                    executor,
                    "_run_preprocessing",
                    return_value="Test task",
                ):
                    # Mock post-processing
                    with patch.object(executor, "_run_postprocessing", return_value=None):
                        await executor.execute(instance)

        # Verify instance is completed
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "completed"

    @pytest.mark.asyncio
    async def test_failure_dispatches_error_notification(self, repo: TaskRepository) -> None:
        """AC: Executor dispatches error notification on failure."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings()
        bus = EventBus()

        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            cwd="/tmp",
        )

        # Mock SDK client to raise exception
        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client_class.side_effect = Exception("SDK error")

            await executor.execute(instance)

        # Verify instance is failed
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "failed"

        # Verify error notification dispatched
        assert len(dispatched_events) == 1
        assert isinstance(dispatched_events[0], TaskNotification)
        assert dispatched_events[0].severity == "error"

    @pytest.mark.asyncio
    async def test_no_notification_when_notify_null(self, repo: TaskRepository) -> None:
        """AC: No notification when definition.notify is null on completion."""
        # Create definition with notify=None
        definition = _make_definition(
            "def-1",
            task_type="background",
            notify=None,
        )
        await repo.create_definition(definition)

        instance = _make_instance(
            "inst-1",
            definition_id="def-1",
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings()
        bus = EventBus()

        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            cwd="/tmp",
        )

        # Mock SDK client and evaluator
        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()

            mock_result = MagicMock()
            mock_result.session_id = "sdk-session-123"
            mock_result.content = [MagicMock(text="Done")]

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive

            with patch("claude_agent_sdk.query") as mock_query:
                eval_result = MagicMock()
                eval_result.content = [MagicMock(text='{"status": "complete", "feedback": "Done"}')]

                async def mock_eval_query(*args, **kwargs):
                    yield eval_result

                mock_query.return_value = mock_eval_query()

                with patch.object(executor, "_run_preprocessing", return_value="Test task"):
                    with patch.object(executor, "_run_postprocessing", return_value=None):
                        await executor.execute(instance)

        # No notification should be dispatched (notify is null)
        assert len(dispatched_events) == 0

    @pytest.mark.asyncio
    async def test_max_iterations_marks_failed(self, repo: TaskRepository) -> None:
        """AC: Executor marks failed when max iterations reached."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(max_iterations=2)
        bus = EventBus()
        bus.dispatch = AsyncMock()

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            cwd="/tmp",
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()

            mock_result = MagicMock()
            mock_result.session_id = "sdk-session-123"
            mock_result.content = [MagicMock(text="Working...")]

            async def mock_receive():
                yield mock_result

            mock_client.receive_response = mock_receive

            with patch("claude_agent_sdk.query") as mock_query:
                eval_result = MagicMock()
                # Evaluator always says continue
                eval_result.content = [MagicMock(text='{"status": "continue", "feedback": "Keep going"}')]

                async def mock_eval_query(*args, **kwargs):
                    yield eval_result

                mock_query.return_value = mock_eval_query()

                with patch.object(executor, "_run_preprocessing", return_value="Test task"):
                    await executor.execute(instance)

        # Verify instance is failed due to max iterations
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "failed"
        assert "max iterations" in updated.result.lower()
