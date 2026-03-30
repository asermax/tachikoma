"""Tests for background task executor."""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.config import TaskSettings
from tachikoma.tasks.events import TaskNotification
from tachikoma.tasks.executor import (
    BackgroundTaskExecutor,
    _PreprocessingResult,
    background_task_runner,
)
from tachikoma.tasks.repository import TaskRepository

from .conftest import _make_definition, _make_instance


def _mock_skill_registry() -> MagicMock:
    return MagicMock()


def _mock_session_registry() -> MagicMock:
    registry = MagicMock()
    registry.mark_processed = AsyncMock()
    return registry


def _mock_preproc_result(prompt: str = "Test task") -> _PreprocessingResult:
    return _PreprocessingResult(prompt=prompt)


def _make_sdk_response(
    text: str = "Task done",
    session_id: str | None = "sdk-session-123",
):
    """Create a mock SDK response async generator function for receive_response."""

    async def _stream():
        yield AssistantMessage(content=[TextBlock(text=text)], model="test")
        if session_id is not None:
            yield ResultMessage(
                subtype="success",
                duration_ms=0,
                duration_api_ms=0,
                is_error=False,
                num_turns=1,
                session_id=session_id,
            )

    return _stream


def _make_eval_response(text: str = '{"status": "complete", "feedback": "Done"}'):
    """Create a mock evaluator response async generator."""

    async def _stream():
        yield AssistantMessage(content=[TextBlock(text=text)], model="test")

    return _stream()


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
                background_task_runner(
                    repo,
                    settings,
                    bus,
                    AgentDefaults(cwd=Path("/tmp")),
                    _mock_skill_registry(),
                    _mock_session_registry(),
                )
            )
            await asyncio.sleep(0.2)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
                background_task_runner(
                    repo,
                    settings,
                    bus,
                    AgentDefaults(cwd=Path("/tmp")),
                    _mock_skill_registry(),
                    _mock_session_registry(),
                )
            )
            await asyncio.sleep(0.5)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
                background_task_runner(
                    repo,
                    settings,
                    bus,
                    AgentDefaults(cwd=Path("/tmp")),
                    _mock_skill_registry(),
                    _mock_session_registry(),
                )
            )
            await asyncio.sleep(0.2)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        # Mock SDK client and evaluator
        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            # Mock query and receive_response
            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(
                text="Task done",
                session_id="sdk-session-123",
            )

            # Mock evaluator to return complete
            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response()

                # Mock pre-processing to return original prompt
                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                ):
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
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
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
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        # Mock SDK client and evaluator
        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Done")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response()

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                ):
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
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Working...")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "continue", "feedback": "Keep going"}',
                )

                with patch.object(
                    executor,
                    "_run_preprocessing",
                    return_value=_mock_preproc_result(),
                ):
                    await executor.execute(instance)

        # Verify instance is failed due to max iterations
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "failed"
        assert "max iterations" in updated.result.lower()


class TestNotificationGeneration:
    """Tests for fork-based notification generation."""

    @pytest.mark.asyncio
    async def test_success_with_notify_forks_session(self, repo: TaskRepository) -> None:
        """AC1: Success + notify set → fork called, generated text used."""
        definition = _make_definition(
            "def-1",
            task_type="background",
            notify="Summarize what you accomplished",
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

        dispatched_events: list[TaskNotification] = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Task done")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response()

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                    patch(
                        "tachikoma.tasks.executor.fork_and_capture",
                        return_value="Task completed: updated 3 files",
                    ) as mock_fork,
                ):
                    await executor.execute(instance)

        # Verify fork was called with the notify prompt
        mock_fork.assert_awaited_once()
        call_args = mock_fork.call_args
        assert call_args[0][1] == "Summarize what you accomplished"

        # Verify notification uses coordinator-routed prompt with fork output
        assert len(dispatched_events) == 1
        assert "Task completed: updated 3 files" in dispatched_events[0].prompt
        assert dispatched_events[0].severity == "info"

    @pytest.mark.asyncio
    async def test_fork_failure_falls_back_to_evaluator_feedback(
        self,
        repo: TaskRepository,
    ) -> None:
        """AC3: Fork failure → falls back to evaluator feedback."""
        definition = _make_definition(
            "def-1",
            task_type="background",
            notify="Summarize results",
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

        dispatched_events: list[TaskNotification] = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Done")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "complete", "feedback": "Evaluator says done"}',
                )

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                    patch(
                        "tachikoma.tasks.executor.fork_and_capture",
                        side_effect=RuntimeError("Fork failed"),
                    ),
                ):
                    await executor.execute(instance)

        # Verify fallback: prompt includes evaluator feedback since fork failed
        assert len(dispatched_events) == 1
        assert "Evaluator says done" in dispatched_events[0].prompt
        assert dispatched_events[0].severity == "info"

    @pytest.mark.asyncio
    async def test_no_sdk_session_id_falls_back(self, repo: TaskRepository) -> None:
        """AC4: No sdk_session_id → skips fork, uses evaluator feedback."""
        definition = _make_definition(
            "def-1",
            task_type="background",
            notify="Summarize results",
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

        dispatched_events: list[TaskNotification] = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()

            # Response with NO session_id
            mock_client.receive_response = _make_sdk_response(text="Done", session_id=None)

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "complete", "feedback": "Evaluator feedback"}',
                )

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                    patch(
                        "tachikoma.tasks.executor.fork_and_capture",
                    ) as mock_fork,
                ):
                    await executor.execute(instance)

        # Fork should NOT have been called
        mock_fork.assert_not_awaited()

        # Fallback to evaluator feedback in prompt
        assert len(dispatched_events) == 1
        assert "Evaluator feedback" in dispatched_events[0].prompt

    @pytest.mark.asyncio
    async def test_error_with_notify_set_bypasses_fork(self, repo: TaskRepository) -> None:
        """AC2: Error severity with notify set → raw error message, no fork."""
        definition = _make_definition(
            "def-1",
            task_type="background",
            notify="Summarize results",
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

        dispatched_events: list[TaskNotification] = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Stuck in loop")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "stuck", "feedback": "Agent is looping"}',
                )

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch(
                        "tachikoma.tasks.executor.fork_and_capture",
                    ) as mock_fork,
                ):
                    await executor.execute(instance)

        # Fork should NOT have been called for error notifications
        mock_fork.assert_not_awaited()

        # Error notification with error prompt, not the notify prompt
        assert len(dispatched_events) == 1
        assert dispatched_events[0].severity == "error"
        assert "Agent is looping" in dispatched_events[0].prompt

    @pytest.mark.asyncio
    async def test_transient_instance_no_fork(self, repo: TaskRepository) -> None:
        """AC5: Transient instance (no definition) → no fork, no notification."""
        instance = _make_instance(
            "inst-1",
            definition_id=None,
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings()
        bus = EventBus()

        dispatched_events: list[TaskNotification] = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Done")

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response()

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                    patch(
                        "tachikoma.tasks.executor.fork_and_capture",
                    ) as mock_fork,
                ):
                    await executor.execute(instance)

        # No fork should have been called, no notification dispatched
        mock_fork.assert_not_awaited()
        assert len(dispatched_events) == 0
