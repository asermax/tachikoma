"""Tests for workflow failure processor."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bubus import EventBus

from tachikoma.sessions.model import Session
from tachikoma.tasks.model import TaskInstance
from tachikoma.workflows.failure_processor import WorkflowFailureProcessor


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _make_instance(
    *,
    workflow_id: str | None = None,
    status: str = "failed",
    prompt: str = "01-plan",
    result: str = "Max iterations reached",
    instance_id: str = "test-inst",
) -> TaskInstance:
    return TaskInstance(
        id=instance_id,
        definition_id=None,
        task_type="background",
        status=status,
        prompt=prompt,
        scheduled_for=_utcnow(),
        started_at=_utcnow(),
        completed_at=_utcnow(),
        result=result,
        sdk_session_id="sdk-123",
        user_response=None,
        updated_at=_utcnow(),
        created_at=_utcnow(),
        workflow_id=workflow_id,
    )


def _make_session() -> Session:
    return Session(
        id="background-task",
        sdk_session_id="sdk-123",
        started_at=_utcnow(),
        ended_at=_utcnow(),
        summary=None,
        transcript_path=None,
    )


class TestSelfSelection:
    """WorkflowFailureProcessor only acts on instances with a workflow_id."""

    async def test_no_op_when_no_workflow_id(self) -> None:
        instance = _make_instance(workflow_id=None, status="failed")
        repo = AsyncMock()
        bus = EventBus()

        processor = WorkflowFailureProcessor(instance, repo, bus)
        await processor.process(_make_session())

        repo.get.assert_not_awaited()
        repo.abort_cascade.assert_not_awaited()


class TestAbortCascade:
    """When self-selected, abort cascade is called correctly."""

    async def test_aborts_cascade_on_failed_workflow_instance(self) -> None:
        instance = _make_instance(
            workflow_id="wf-123",
            status="failed",
            prompt="01-plan",
            result="Agent stuck: couldn't proceed",
        )
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="test-skill",
            workflow_name="test-workflow",
            scratchpad_path="/tmp/scratch-wf-123.md",
        )
        repo.abort_cascade.return_value = ["wf-123", "child-1"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ),
        ):
            await processor.process(_make_session())

        repo.abort_cascade.assert_awaited_once_with("wf-123")

    async def test_deletes_scratchpad(self) -> None:
        instance = _make_instance(workflow_id="wf-456", status="failed")
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="skill",
            workflow_name="workflow",
            scratchpad_path="/tmp/scratch-456.md",
        )
        repo.abort_cascade.return_value = ["wf-456"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad") as mock_delete,
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ),
        ):
            await processor.process(_make_session())

        mock_delete.assert_called_once_with("/tmp/scratch-456.md")

    async def test_dispatches_failure_notification(self) -> None:
        instance = _make_instance(
            workflow_id="wf-789",
            status="failed",
            prompt="02-execute",
            result="Max iterations reached",
        )
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="my-skill",
            workflow_name="my-workflow",
            scratchpad_path="/tmp/scratch-789.md",
        )
        repo.abort_cascade.return_value = ["wf-789"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ) as mock_notif,
        ):
            await processor.process(_make_session())

        mock_notif.assert_awaited_once()
        call_kwargs = mock_notif.call_args
        assert call_kwargs[0][0] is bus  # bus
        assert "Workflow: my-skill/my-workflow" in call_kwargs[0][1]  # source
        assert "02-execute" in call_kwargs[0][2]  # content
        assert "Max iterations reached" in call_kwargs[0][2]

    async def test_notification_source_fallback_when_state_missing(self) -> None:
        instance = _make_instance(workflow_id="wf-missing", status="failed")
        repo = AsyncMock()
        repo.get.return_value = None
        repo.abort_cascade.return_value = ["wf-missing"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ) as mock_notif,
        ):
            await processor.process(_make_session())

        mock_notif.assert_awaited_once()
        assert mock_notif.call_args[0][1] == "Workflow: wf-missing"


class TestErrorIsolation:
    """Errors within the processor are swallowed."""

    async def test_abort_cascade_failure_is_swallowed(self) -> None:
        instance = _make_instance(workflow_id="wf-err", status="failed")
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="skill",
            workflow_name="workflow",
            scratchpad_path="/tmp/scratch.md",
        )
        repo.abort_cascade.side_effect = RuntimeError("DB error")

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ),
        ):
            # Should not raise
            await processor.process(_make_session())

    async def test_scratchpad_deletion_failure_is_swallowed(self) -> None:
        instance = _make_instance(workflow_id="wf-scratch", status="failed")
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="skill",
            workflow_name="workflow",
            scratchpad_path="/tmp/scratch.md",
        )
        repo.abort_cascade.return_value = ["wf-scratch"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch(
                "tachikoma.workflows.failure_processor.delete_scratchpad",
                side_effect=OSError("Permission denied"),
            ),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ),
        ):
            await processor.process(_make_session())

    async def test_notification_failure_is_swallowed(self) -> None:
        instance = _make_instance(workflow_id="wf-notif", status="failed")
        repo = AsyncMock()
        repo.get.return_value = MagicMock(
            skill_name="skill",
            workflow_name="workflow",
            scratchpad_path="/tmp/scratch.md",
        )
        repo.abort_cascade.return_value = ["wf-notif"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Bus error"),
            ),
        ):
            await processor.process(_make_session())

    async def test_get_state_failure_is_swallowed(self) -> None:
        instance = _make_instance(workflow_id="wf-state-err", status="failed")
        repo = AsyncMock()
        repo.get.side_effect = RuntimeError("DB error")
        repo.abort_cascade.return_value = ["wf-state-err"]

        bus = EventBus()
        processor = WorkflowFailureProcessor(instance, repo, bus)

        with (
            patch("tachikoma.workflows.failure_processor.delete_scratchpad"),
            patch(
                "tachikoma.workflows.failure_processor.dispatch_notification",
                new_callable=AsyncMock,
            ),
        ):
            await processor.process(_make_session())


class TestStatusMessage:
    async def test_status_message(self) -> None:
        instance = _make_instance()
        processor = WorkflowFailureProcessor(instance, AsyncMock(), EventBus())
        assert "failure" in processor.status_message().lower()
