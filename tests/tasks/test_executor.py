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
from tachikoma.notifications import Notification
from tachikoma.tasks.executor import (
    BackgroundTaskExecutor,
    _PreprocessingResult,
    background_task_runner,
)
from tachikoma.tasks.repository import TaskRepository

from .conftest import _make_instance


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
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(max_concurrent_background=1)
        bus = EventBus()

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
        for i in range(5):
            instance = _make_instance(
                f"inst-{i}",
                task_type="background",
                status="pending",
            )
            await repo.create_instance(instance)

        settings = TaskSettings(max_concurrent_background=2)
        bus = EventBus()

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

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(
                text="Task done",
                session_id="sdk-session-123",
            )

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

        # Verify instance is completed
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "completed"

    @pytest.mark.asyncio
    async def test_failure_dispatches_error_notification(self, repo: TaskRepository) -> None:
        """AC: Executor dispatches error Notification on failure."""
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
        assert isinstance(dispatched_events[0], Notification)
        assert dispatched_events[0].severity == "error"
        assert dispatched_events[0].source_id == "inst-1"

    @pytest.mark.asyncio
    async def test_no_notification_on_success(self, repo: TaskRepository) -> None:
        """AC: No notification on success — agent controls notifications."""
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

        # No notification should be dispatched on success
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

        # Verify error notification dispatched
        assert len(dispatched_events) == 1
        assert isinstance(dispatched_events[0], Notification)
        assert dispatched_events[0].severity == "error"

    @pytest.mark.asyncio
    async def test_notification_server_registered_in_sdk_options(
        self, repo: TaskRepository
    ) -> None:
        """AC: Notification MCP server is present in SDK options mcp_servers."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="pending",
            prompt="Test task",
        )
        await repo.create_instance(instance)

        settings = TaskSettings()
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

        captured_mcp_servers = {}

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            # Capture the options passed to ClaudeSDKClient constructor
            def capture_options(options):
                captured_mcp_servers.update(options.mcp_servers or {})
                return mock_client

            mock_client_class.side_effect = capture_options

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

        # Verify the notification server was added to mcp_servers
        assert "notifications" in captured_mcp_servers

    @pytest.mark.asyncio
    async def test_agent_notification_and_failure_both_delivered(
        self, repo: TaskRepository
    ) -> None:
        """AC: Agent and failure notifications dispatched independently.

        Simulates an agent calling send_notification during execution (dispatching directly
        to the bus) followed by a failure — both notifications should be present.
        """
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

        agent_notification = Notification(
            prompt="Progress update from agent",
            source_id="inst-1",
            severity="info",
        )

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            # Simulate agent calling send_notification during execution,
            # then evaluator says stuck
            async def mock_receive_response():
                # Agent sends notification during execution (simulating tool call)
                dispatched_events.append(agent_notification)
                async for msg in _make_sdk_response(text="Working...")():
                    yield msg

            mock_client_class.return_value = mock_client
            mock_client.query = AsyncMock()
            mock_client.receive_response = mock_receive_response

            with patch("claude_agent_sdk.query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "stuck", "feedback": "Agent is looping"}',
                )

                with patch.object(
                    executor,
                    "_run_preprocessing",
                    return_value=_mock_preproc_result(),
                ):
                    await executor.execute(instance)

        # Both the agent notification and the failure notification should be present
        assert len(dispatched_events) == 2

        # Agent's info notification
        info_events = [e for e in dispatched_events if e.severity == "info"]
        assert len(info_events) == 1

        # Executor's error notification
        error_events = [e for e in dispatched_events if e.severity == "error"]
        assert len(error_events) == 1
        assert "failed" in error_events[0].prompt.lower()
