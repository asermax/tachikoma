"""Stale workflow cleanup processor.

Soft-deletes workflow states that haven't been updated within a configurable
threshold (default 24 hours) and removes their associated scratchpad files.

Runs as a post-processor in the pre_finalize phase, committed alongside
session changes.
"""

from datetime import timedelta

from loguru import logger

from tachikoma.post_processing import PostProcessor
from tachikoma.sessions.model import Session
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import _delete_scratchpad

_log = logger.bind(component="workflow_cleanup")

DEFAULT_STALE_THRESHOLD = timedelta(hours=24)


class StaleWorkflowCleanupProcessor(PostProcessor):
    """Post-processor that cleans up stale workflow states.

    Soft-deletes workflow state records whose updated_at is older than
    the configured threshold, and removes their scratchpad files.
    """

    _status_message = "Cleaning up workflows..."

    def __init__(
        self,
        repository: WorkflowStateRepository,
        threshold: timedelta = DEFAULT_STALE_THRESHOLD,
    ) -> None:
        self._repository = repository
        self._threshold = threshold

    async def process(self, session: Session, *, extra: dict | None = None) -> None:
        """Find and clean up stale workflow states."""
        try:
            stale = await self._repository.list_stale(self._threshold)
        except Exception as exc:
            _log.exception("Failed to list stale workflows: err={err}", err=str(exc))
            return

        if not stale:
            return

        cleaned = 0
        for state in stale:
            try:
                deleted = await self._repository.soft_delete(state.id)
                if deleted:
                    _delete_scratchpad(state.scratchpad_path)
                    cleaned += 1
            except Exception as exc:
                _log.warning(
                    "Failed to clean up stale workflow: id={id}, err={err}",
                    id=state.id,
                    err=str(exc),
                )

        if cleaned > 0:
            _log.info(
                "Cleaned up {count} stale workflow(s) (threshold={threshold})",
                count=cleaned,
                threshold=self._threshold,
            )
