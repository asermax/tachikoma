"""Tests for background task executor."""

import asyncio
import contextlib
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bubus import EventBus
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.buffer.priority import Priority
from tachikoma.config import TaskSettings
from tachikoma.notifications import Notification, handle_send_notification
from tachikoma.tasks.executor import (
    BACKGROUND_TASK_SYSTEM_PROMPT,
    BackgroundTaskExecutor,
    _PreprocessingResult,
    _sweep_expired_waiters,
    background_task_runner,
)
from tachikoma.tasks.repository import TaskRepository

from .conftest import _make_instance, _utcnow


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


def _make_eval_response(text: str = '{"status": "complete", "rationale": "Done"}'):
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

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
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

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
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

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "continue", "rationale": "Keep going"}',
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
        """AC: Notification MCP server and injected extra servers are present in mcp_servers."""
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

        sentinel_git = MagicMock()
        sentinel_tasks = MagicMock()

        executor = BackgroundTaskExecutor(
            repository=repo,
            settings=settings,
            bus=bus,
            agent_defaults=AgentDefaults(cwd=Path("/tmp")),
            skill_registry=_mock_skill_registry(),
            session_registry=_mock_session_registry(),
            extra_mcp_servers={"git-tools": sentinel_git, "task-tools": sentinel_tasks},
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

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
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

        # Verify the per-execution notification server and always-on extra servers are merged
        assert "notifications" in captured_mcp_servers
        assert captured_mcp_servers.get("git-tools") is sentinel_git
        assert captured_mcp_servers.get("task-tools") is sentinel_tasks

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

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            # Simulate agent calling send_notification during execution,
            # then evaluator says stuck
            async def mock_receive_response():
                # Agent sends notification via the actual handler (end-to-end path)
                await handle_send_notification(
                    message="Progress update: halfway through",
                    bus=bus,
                    source="Background task: Test task",
                    source_id="inst-1",
                )
                async for msg in _make_sdk_response(text="Working...")():
                    yield msg

            mock_client_class.return_value = mock_client
            mock_client.query = AsyncMock()
            mock_client.receive_response = mock_receive_response

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "stuck", "rationale": "Agent is looping"}',
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

    @pytest.mark.asyncio
    async def test_needs_input_transitions_to_waiting(self, repo: TaskRepository) -> None:
        """AC (DLT-120): Executor transitions to waiting on needs_input."""
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
                text="What format?",
                session_id="sdk-session-456",
            )

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "needs_input", "rationale": "What format should I use?"}',
                )

                with patch.object(
                    executor,
                    "_run_preprocessing",
                    return_value=_mock_preproc_result(),
                ):
                    await executor.execute(instance)

        # Verify instance is waiting
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "waiting"
        assert updated.sdk_session_id == "sdk-session-456"

        # Verify respondable urgent notification was dispatched
        assert len(dispatched_events) == 1
        notif = dispatched_events[0]
        assert isinstance(notif, Notification)
        assert notif.priority == Priority.URGENT
        assert notif.response_instance_id == "inst-1"
        assert "respond_to_task" in notif.prompt
        assert "What format should I use?" in notif.prompt


class TestExecutorStderrCapture:
    """Tests for DLT-098: stderr capture in executor error handler."""

    @pytest.mark.asyncio
    async def test_stderr_accumulator_installed_on_options(
        self, repo: TaskRepository, mocker
    ) -> None:
        """AC: DLT-098 R1 — StderrAccumulator installed on SDK options."""
        captured_options = []

        class CapturingClient:
            def __init__(self, opts, **kwargs):
                captured_options.append(opts)

            async def __aenter__(self):
                raise RuntimeError("crash before any work")

            async def __aexit__(self, *args):
                pass

        instance = _make_instance(
            "inst-verify-stderr",
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

        with (
            patch("tachikoma.tasks.executor.ClaudeSDKClient", CapturingClient),
            patch.object(
                executor,
                "_run_preprocessing",
                return_value=_mock_preproc_result(),
            ),
        ):
            await executor.execute(instance)

        # Verify stderr accumulator was installed
        assert len(captured_options) == 1
        assert captured_options[0].stderr is not None

        # Feed it and verify accumulation works
        captured_options[0].stderr("error line")
        acc = captured_options[0].stderr
        assert acc.get() == "error line"

    @pytest.mark.asyncio
    async def test_no_stderr_in_log_when_empty_executor(self, repo: TaskRepository, mocker) -> None:
        """AC: DLT-098 R0 — executor error with no stderr omits stderr kwarg."""
        instance = _make_instance(
            "inst-no-stderr",
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

        mock_log = mocker.patch("tachikoma.tasks.executor._log")

        class CrashingClient:
            def __init__(self, opts, **kwargs):
                pass

            async def __aenter__(self):
                raise RuntimeError("SDK crashed")

            async def __aexit__(self, *args):
                pass

        with (
            patch("tachikoma.tasks.executor.ClaudeSDKClient", CrashingClient),
            patch.object(
                executor,
                "_run_preprocessing",
                return_value=_mock_preproc_result(),
            ),
        ):
            await executor.execute(instance)

        exception_calls = mock_log.exception.call_args_list
        assert len(exception_calls) > 0
        # No stderr kwarg when buffer empty
        last_call_kwargs = exception_calls[-1][1]
        assert "stderr" not in last_call_kwargs


class TestBackgroundTaskSystemPrompt:
    """Tests for system prompt documentation (DLT-112 R8)."""

    def test_mentions_all_priority_levels(self) -> None:
        assert "urgent" in BACKGROUND_TASK_SYSTEM_PROMPT
        assert "normal" in BACKGROUND_TASK_SYSTEM_PROMPT
        assert "low" in BACKGROUND_TASK_SYSTEM_PROMPT
        assert "priority" in BACKGROUND_TASK_SYSTEM_PROMPT

    def test_mentions_asking_questions_section(self) -> None:
        """AC (DLT-120): System prompt explains evaluator-mediated communication."""
        assert "evaluator" in BACKGROUND_TASK_SYSTEM_PROMPT.lower()
        assert "notification" in BACKGROUND_TASK_SYSTEM_PROMPT.lower()


class TestExecutorResumePath:
    """Tests for the resume path in executor (DLT-120)."""

    @pytest.mark.asyncio
    async def test_resume_with_response_completes(self, repo: TaskRepository, tmp_path) -> None:
        """AC: Waiting instance with response resumes and completes."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="waiting",
            prompt="Test task",
            sdk_session_id="sdk-session-456",
            user_response="Yes, proceed",
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

        with patch("tachikoma.tasks.executor.ClaudeSDKClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client

            mock_client.query = AsyncMock()
            mock_client.receive_response = _make_sdk_response(text="Done")

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "complete", "rationale": "Done"}',
                )

                with (
                    patch.object(
                        executor,
                        "_run_preprocessing",
                        return_value=_mock_preproc_result(),
                    ),
                    patch.object(executor, "_run_postprocessing", return_value=None),
                ):
                    await executor.execute(instance)

        # Verify instance completed
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "completed"

        # Verify the SDK was called with resume= option
        call_kwargs = mock_client_class.call_args
        assert call_kwargs.kwargs.get("resume") == "sdk-session-456" or (
            len(call_kwargs.args) > 0 and hasattr(call_kwargs.args[0], "resume")
        )

    @pytest.mark.asyncio
    async def test_resume_surfaces_sdk_errors_as_failure(self, repo: TaskRepository) -> None:
        """Errors raised by the SDK on resume mark the instance failed."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="waiting",
            prompt="Test task",
            sdk_session_id="sdk-session-456",
            user_response="Yes",
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
            mock_client_class.side_effect = RuntimeError("transcript missing")

            with patch.object(
                executor,
                "_run_preprocessing",
                return_value=_mock_preproc_result(),
            ):
                await executor.execute(instance)

        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "failed"
        assert "transcript missing" in (updated.result or "")

        assert len(dispatched_events) == 1
        notif = dispatched_events[0]
        assert isinstance(notif, Notification)
        assert notif.response_instance_id is None

    @pytest.mark.asyncio
    async def test_resume_can_re_enter_waiting(self, repo: TaskRepository) -> None:
        """AC: Resumed task can re-enter waiting on second needs_input."""
        instance = _make_instance(
            "inst-1",
            task_type="background",
            status="waiting",
            prompt="Test task",
            sdk_session_id="sdk-session-456",
            user_response="First answer",
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
                text="Another question?",
                session_id="sdk-session-789",
            )

            with patch("tachikoma.sdk_query.stderr_aware_query") as mock_query:
                mock_query.return_value = _make_eval_response(
                    '{"status": "needs_input", "rationale": "Another question?"}',
                )

                with patch.object(
                    executor,
                    "_run_preprocessing",
                    return_value=_mock_preproc_result(),
                ):
                    await executor.execute(instance)

        # Verify instance is back in waiting with updated session
        updated = await repo.get_instance("inst-1")
        assert updated is not None
        assert updated.status == "waiting"
        assert updated.sdk_session_id == "sdk-session-789"

        # Verify new respondable notification
        assert len(dispatched_events) == 1
        notif = dispatched_events[0]
        assert notif.response_instance_id == "inst-1"


class TestRunnerTimeoutSweep:
    """Tests for the runner timeout sweep (DLT-120 S8)."""

    @pytest.mark.asyncio
    async def test_expired_waiting_instance_swept(self, repo: TaskRepository) -> None:
        """AC: Expired waiting instance is marked failed with notification."""
        old_time = _utcnow() - timedelta(seconds=7210)
        await repo.create_instance(
            _make_instance(
                "expired-1",
                task_type="background",
                status="waiting",
                updated_at=old_time,
            )
        )

        settings = TaskSettings(wait_timeout=7200)
        bus = EventBus()

        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        await _sweep_expired_waiters(repo, settings, bus)

        # Verify instance failed
        updated = await repo.get_instance("expired-1")
        assert updated is not None
        assert updated.status == "failed"
        assert "timed out" in (updated.result or "").lower()

        # Verify urgent non-respondable notification
        assert len(dispatched_events) == 1
        notif = dispatched_events[0]
        assert isinstance(notif, Notification)
        assert notif.response_instance_id is None

    @pytest.mark.asyncio
    async def test_runner_uses_ready_instances(self, repo: TaskRepository) -> None:
        """AC: Runner picks up both pending and waiting-with-response instances."""
        await repo.create_instance(
            _make_instance("pending-1", task_type="background", status="pending")
        )
        await repo.create_instance(
            _make_instance(
                "waiting-1",
                task_type="background",
                status="waiting",
                user_response="Yes",
            )
        )

        settings = TaskSettings(max_concurrent_background=2)
        bus = EventBus()

        executed_ids = []

        async def mock_execute(self, inst):
            executed_ids.append(inst.id)
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
            await asyncio.sleep(0.3)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert "pending-1" in executed_ids
        assert "waiting-1" in executed_ids


class TestCrashRecoveryWaiting:
    """Tests for crash recovery with waiting instances (DLT-120 S9)."""

    @pytest.mark.asyncio
    async def test_waiting_survives_bootstrap(self, repo: TaskRepository) -> None:
        """AC: Waiting instances are not affected by mark_running_as_failed."""
        await repo.create_instance(
            _make_instance("waiting-1", status="waiting", task_type="background")
        )
        await repo.create_instance(
            _make_instance("running-1", status="running", task_type="background")
        )

        # Crash recovery only touches running instances
        count = await repo.mark_running_as_failed("system restart")

        assert count == 1

        waiting = await repo.get_instance("waiting-1")
        assert waiting is not None
        assert waiting.status == "waiting"

        running = await repo.get_instance("running-1")
        assert running is not None
        assert running.status == "failed"
