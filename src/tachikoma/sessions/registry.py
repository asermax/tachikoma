"""SessionRegistry: business logic facade for conversation session tracking.

The registry serializes session creation, derives session status, and drives
crash recovery. Delegates all persistence to SessionRepository.
"""

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from tachikoma.sessions.model import Session, SessionContextEntry, SessionResumption
from tachikoma.sessions.repository import SessionRepository

_log = logger.bind(component="sessions")


class SessionRegistry:
    """Facade for session lifecycle management.

    Provides create / close / update / query operations.
    Uses an asyncio.Lock to serialize session creation and prevent duplicates.

    Usage::

        registry = SessionRegistry(repository)
        session = await registry.create_session()
        ...
        await registry.close_session(session.id)
    """

    def __init__(self, repository: SessionRepository) -> None:
        self._repository = repository
        self._lock = asyncio.Lock()
        self._active_session: Session | None = None

    async def create_session(self) -> Session:
        """Create a new conversation session and mark it as active.

        Serialized via an internal asyncio.Lock to prevent duplicate creation
        when concurrent signals arrive simultaneously.
        """
        async with self._lock:
            session = Session(
                id=uuid.uuid4().hex,
                started_at=datetime.now(UTC),
            )

            session = await self._repository.create(session)
            self._active_session = session

            _log.info("Session created: session_id={id}", id=session.id)
            return session

    async def close_session(self, session_id: str) -> bool:
        """Close the session with the given ID by setting ended_at.

        Idempotent: if the session is already closed or doesn't exist, no-op.

        Returns:
            True if the session was actually transitioned from open to closed.
            False if no transition occurred (no-op, already closed, wrong ID).
        """
        if self._active_session is None:
            return False

        if self._active_session.id != session_id:
            return False

        if self._active_session.ended_at is not None:
            self._active_session = None
            return False

        ended_at = datetime.now(UTC)
        await self._repository.update(session_id, ended_at=ended_at)
        self._active_session = None

        _log.info("Session closed: session_id={id}", id=session_id)
        return True

    async def update_metadata(
        self,
        session_id: str,
        sdk_session_id: str,
        transcript_path: str,
    ) -> None:
        """Populate SDK metadata after the coordinator receives a Result event."""
        await self._repository.update(
            session_id,
            sdk_session_id=sdk_session_id,
            transcript_path=transcript_path,
        )

        # Update in-memory active session reference with new metadata
        if self._active_session is not None and self._active_session.id == session_id:
            self._active_session = await self._repository.get_by_id(session_id)

        _log.debug(
            "Session metadata updated: session_id={id} sdk_session_id={sdk}",
            id=session_id,
            sdk=sdk_session_id,
        )

    async def update_summary(self, session_id: str, summary: str) -> None:
        """Update the rolling conversation summary on a session.

        Re-fetches the session after update to replace the frozen dataclass
        reference with the new summary value.

        Args:
            session_id: The ID of the session to update.
            summary: The new conversation summary text.
        """
        await self._repository.update(session_id, summary=summary)

        # Update in-memory active session reference with new summary
        if self._active_session is not None and self._active_session.id == session_id:
            self._active_session = await self._repository.get_by_id(session_id)

        _log.debug(
            "Session summary updated: session_id={id} summary_length={len}",
            id=session_id,
            len=len(summary),
        )

    async def mark_processed(self, session_id: str) -> None:
        """Mark a session as post-processed by setting processed_at.

        Updates the in-memory active session reference if it matches.

        Args:
            session_id: The ID of the session to mark as processed.
        """
        now = datetime.now(UTC)
        await self._repository.update(session_id, processed_at=now)

        if self._active_session is not None and self._active_session.id == session_id:
            self._active_session = replace(self._active_session, processed_at=now)

        _log.debug("Session marked as processed: session_id={id}", id=session_id)

    async def get_active_session(self) -> Session | None:
        """Return the currently active session, or None if no session is open."""
        return self._active_session

    async def reopen_session(self, session_id: str) -> Session | None:
        """Reopen a closed session for resumption.

        Validates that the session exists, is closed, and is not already active.
        On success, clears ended_at and sets last_resumed_at, then updates
        _active_session to the reopened session.

        Args:
            session_id: The ID of the closed session to reopen.

        Returns:
            The reopened Session, or None if validation failed.
        """
        # Fetch the session
        session = await self._repository.get_by_id(session_id)
        if session is None:
            _log.warning(
                "Cannot reopen session: not found session_id={id}",
                id=session_id,
            )
            return None

        # Validate it's closed
        if session.ended_at is None:
            _log.warning(
                "Cannot reopen session: already open session_id={id}",
                id=session_id,
            )
            return None

        # Validate it's not already active (edge case)
        if self._active_session is not None and self._active_session.id == session_id:
            _log.warning(
                "Cannot reopen session: already active session_id={id}",
                id=session_id,
            )
            return None

        now = datetime.now(UTC)

        # Update the session: clear ended_at, set last_resumed_at
        await self._repository.update(
            session_id,
            ended_at=None,
            last_resumed_at=now,
        )

        # Construct the reopened session from known data (avoids a second DB fetch)
        reopened = replace(session, ended_at=None, last_resumed_at=now)
        self._active_session = reopened

        _log.info(
            "Session reopened: session_id={id} previous_ended_at={ts}",
            id=session_id,
            ts=session.ended_at,
        )

        return reopened

    async def get_recent_closed(self, before: datetime, window: timedelta) -> list[Session]:
        """Return recently closed sessions within the time window.

        Delegates to repository. Used by coordinator to find resumption candidates.

        Args:
            before: The reference timestamp (typically now).
            window: How far back to look for closed sessions.
        """
        return await self._repository.get_recent_closed(before, window)

    async def record_resumption(self, session_id: str, previous_ended_at: datetime) -> None:
        """Record a session resumption event.

        Creates a SessionResumption record. Failures are logged but not raised
        (tracking is best-effort per R7).

        Args:
            session_id: The ID of the resumed session.
            previous_ended_at: When the session was closed before this resumption.
        """
        try:
            resumption = SessionResumption(
                session_id=session_id,
                resumed_at=datetime.now(UTC),
                previous_ended_at=previous_ended_at,
            )
            await self._repository.create_resumption(resumption)

            _log.debug(
                "Resumption recorded: session_id={id} previous_ended_at={ts}",
                id=session_id,
                ts=previous_ended_at,
            )
        except Exception as exc:
            # Best-effort tracking: log but don't raise
            _log.warning(
                "Failed to record resumption (best-effort): session_id={id} err={err}",
                id=session_id,
                err=str(exc),
            )

    async def get_by_time_range(self, start: datetime, end: datetime) -> list[Session]:
        """Return sessions whose time span overlaps the given [start, end) range.

        Delegates to repository. Used by coordinator to find intermediate sessions
        for bridging context.

        Args:
            start: Start of the time range.
            end: End of the time range.
        """
        return await self._repository.get_by_time_range(start, end)

    # ------------------------------------------------------------------
    # Context entries
    # ------------------------------------------------------------------

    async def save_context_entries(self, session_id: str, entries: list[tuple[str, str]]) -> None:
        """Save context entries for a session.

        Best-effort persistence: failures are logged but not raised.
        This ensures context persistence failures don't interrupt conversations.

        Args:
            session_id: The session to save entries for.
            entries: List of (owner, content) tuples to persist.
        """
        if not entries:
            return

        try:
            await self._repository.save_context_entries(session_id, entries)
            _log.debug(
                "Context entries saved: session_id={id} count={count}",
                id=session_id,
                count=len(entries),
            )
        except Exception as exc:
            # Best-effort: log but don't raise per R7
            _log.warning(
                "Failed to save context entries (best-effort): session_id={id} err={err}",
                id=session_id,
                err=str(exc),
            )

    async def load_context_entries(self, session_id: str) -> list[SessionContextEntry]:
        """Load all context entries for a session.

        Delegates to repository. Returns entries ordered by insertion order (id asc).

        Args:
            session_id: The session to load entries for.

        Returns:
            List of SessionContextEntry instances, or empty list if none exist.

        Raises:
            SessionRepositoryError: If the load operation fails.
        """
        return await self._repository.load_context_entries(session_id)

    async def recover_interrupted(self) -> None:
        """Close any sessions left open from a previous ungraceful shutdown.

        Uses transcript file mtime as the best-effort end timestamp when the
        SDK session ID is set and the file exists; falls back to current time.
        Idempotent: safe to call on every launch.
        """
        open_sessions = await self._repository.get_open_sessions()

        _log.info("Recovery started: open_count={n}", n=len(open_sessions))

        for session in open_sessions:
            ended_at = _best_effort_end_time(session)
            await self._repository.update(session.id, ended_at=ended_at)

            _log.info(
                "Session recovered: session_id={id} ended_at={ts}",
                id=session.id,
                ts=ended_at,
            )

        _log.info("Recovery completed: recovered_count={n}", n=len(open_sessions))


def _best_effort_end_time(session: Session) -> datetime:
    """Derive the best-effort end timestamp for crash recovery.

    Priority:
    1. If sdk_session_id is set, try to use the transcript file's mtime.
    2. Fall back to current time if the file isn't found or sdk_session_id is None.
    """
    if session.sdk_session_id is not None and session.transcript_path is not None:
        transcript = Path(session.transcript_path)

        if transcript.exists():
            mtime = transcript.stat().st_mtime
            return datetime.fromtimestamp(mtime, tz=UTC)

    return datetime.now(UTC)
