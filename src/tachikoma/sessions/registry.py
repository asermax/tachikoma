"""SessionRegistry: business logic facade for conversation session tracking.

The registry serializes session creation, derives session status, and drives
crash recovery. Delegates all persistence to SessionRepository.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from tachikoma.sessions.model import Session
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

    async def close_session(self, session_id: str) -> None:
        """Close the session with the given ID by setting ended_at.

        Idempotent: if the session is already closed or doesn't exist, no-op.
        """
        if self._active_session is None:
            return

        if self._active_session.ended_at is not None:
            # Already closed — idempotent
            return

        if self._active_session.id != session_id:
            # Close signal for a different session — ignore
            return

        ended_at = datetime.now(UTC)
        await self._repository.update(session_id, ended_at=ended_at)
        self._active_session = None

        _log.info("Session closed: session_id={id}", id=session_id)

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

    async def get_active_session(self) -> Session | None:
        """Return the currently active session, or None if no session is open."""
        return self._active_session

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
