"""Workflow failure processor for abort cascade on step task failure.

Runs as a PostProcessor in the background task executor's adapted pipeline.
Registered only on failure paths (via ``is_failure=True``), so it always acts
when present. Aborts the entire workflow cascade, cleans up the scratchpad,
and dispatches a failure notification.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import dispatch_notification
from tachikoma.post_processing import MAIN_PHASE, PostProcessor
from tachikoma.sessions.model import Session
from tachikoma.tasks.model import TaskInstance
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import delete_scratchpad

if TYPE_CHECKING:
    from bubus import EventBus

_log = logger.bind(component="workflow_failure_processor")


class WorkflowFailureProcessor(PostProcessor):
    """Detects failed workflow step tasks and aborts the workflow cascade.

    Registered only on failure paths — always acts when ``instance.workflow_id``
    is set. The executor's ``_run_postprocessing`` determines relevance via
    ``is_failure``, so this processor does not re-check status (which would be
    stale anyway since TaskInstance is a frozen dataclass).

    Errors within the processor are logged and swallowed — matching
    post-processing error isolation semantics (failure to clean up workflow
    state should not prevent the executor from completing its error handling).
    """

    phase = MAIN_PHASE
    _status_message = "Handling workflow step failure..."

    def __init__(
        self,
        instance: TaskInstance,
        repository: WorkflowStateRepository,
        bus: EventBus,
    ) -> None:
        self._instance = instance
        self._repository = repository
        self._bus = bus

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        if self._instance.workflow_id is None:
            return

        workflow_id = self._instance.workflow_id
        _log.info(
            "Workflow step failed, aborting cascade: workflow_id={wid} instance={iid}",
            wid=workflow_id,
            iid=self._instance.id,
        )

        try:
            state = await self._repository.get(workflow_id)
        except Exception:
            _log.exception(
                "Failed to read workflow state for abort: workflow_id={wid}",
                wid=workflow_id,
            )
            state = None

        if state is not None:
            notification_source = f"Workflow: {state.skill_name}/{state.workflow_name}"
        else:
            notification_source = f"Workflow: {workflow_id}"

        try:
            ids = await self._repository.abort_cascade(workflow_id)
            _log.info(
                "Abort cascade completed: workflow_id={wid} records_deleted={count}",
                wid=workflow_id,
                count=len(ids),
            )
        except Exception:
            _log.exception(
                "Failed to abort workflow cascade: workflow_id={wid}",
                wid=workflow_id,
            )

        if state is not None:
            try:
                delete_scratchpad(state.scratchpad_path)
            except Exception:
                _log.exception(
                    "Failed to delete scratchpad: path={path}",
                    path=state.scratchpad_path,
                )

        try:
            reason = self._instance.result or "unknown error"
            step_info = f"step '{self._instance.prompt}'" if self._instance.prompt else "a step"
            await dispatch_notification(
                self._bus,
                notification_source,
                f"Workflow failed at {step_info}: {reason}",
                "error",
                self._instance.id,
                priority=Priority.URGENT,
            )
        except Exception:
            _log.exception(
                "Failed to dispatch workflow failure notification: workflow_id={wid}",
                wid=workflow_id,
            )
