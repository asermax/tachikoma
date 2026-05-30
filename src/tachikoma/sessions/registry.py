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

from tachikoma.sessions.model import (
    ChannelMessage,
    MessageDirection,
    Session,
    SessionContextEntry,
    SessionResumption,
)
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

    def __init__(
        self,
        repository: SessionRepository,
        max_session_age: timedelta = timedelta(hours=24),
    ) -> None:
        self._repository = repository
        self._max_session_age = max_session_age
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

    async def update_last_exchange(self, session_id: str, last_exchange: str) -> None:
        """Update the last assistant response on a session.

        Args:
            session_id: The ID of the session to update.
            last_exchange: The last assistant response text.
        """
        await self._repository.update(session_id, last_exchange=last_exchange)

        if self._active_session is not None and self._active_session.id == session_id:
            self._active_session = replace(self._active_session, last_exchange=last_exchange)

        _log.debug(
            "Session last_exchange updated: session_id={id} last_exchange_length={len}",
            id=session_id,
            len=len(last_exchange),
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

    async def mark_errored(self, session_id: str) -> None:
        """Mark a session as errored to prevent resuming a contaminated session.

        Sets the error flag on the session, which excludes it from resumable
        candidates in get_recent_closed() and makes its status "interrupted".

        Args:
            session_id: The ID of the session to mark as errored.
        """
        await self._repository.update(session_id, error=True)

        if self._active_session is not None and self._active_session.id == session_id:
            self._active_session = await self._repository.get_by_id(session_id)

        _log.info("Session marked as errored: session_id={id}", id=session_id)

    async def get_active_session(self) -> Session | None:
        """Return the currently active session, or None if no session is open."""
        return self._active_session

    async def _validate_reopen_preconditions(
        self, session_id: str, *, skip_age_check: bool = False
    ) -> Session | None:
        """Check all reopen preconditions, returning the session if valid."""
        session = await self._repository.get_by_id(session_id)
        if session is None:
            _log.warning("Cannot reopen session: not found session_id={id}", id=session_id)
            return None

        if session.transcript_path is None:
            _log.warning("Cannot reopen session: no transcript_path session_id={id}", id=session_id)
            return None

        if not Path(session.transcript_path).exists():
            _log.warning(
                "Cannot reopen session: transcript not exists locally session_id={id} path={path}",
                id=session_id,
                path=session.transcript_path,
            )
            return None

        if not skip_age_check and datetime.now(UTC) - session.started_at > self._max_session_age:
            _log.warning(
                "Cannot reopen session: too old session_id={id} started_at={ts}",
                id=session_id,
                ts=session.started_at.isoformat(),
            )
            return None

        if session.ended_at is None:
            _log.warning("Cannot reopen session: already open session_id={id}", id=session_id)
            return None

        if self._active_session is not None and self._active_session.id == session_id:
            _log.warning("Cannot reopen session: already active session_id={id}", id=session_id)
            return None

        return session

    async def reopen_session(
        self, session_id: str, *, skip_age_check: bool = False
    ) -> Session | None:
        """Reopen a closed session for resumption.

        Validates transcript availability, session age, and state before allowing
        resumption. On failure, returns None and logs a warning.

        Args:
            session_id: The ID of the closed session to reopen.
            skip_age_check: When True, bypass the max_session_age check.
                Use for explicit user-initiated routing (reply-to) where the
                user's intent is a stronger signal than the age safeguard.

        Returns:
            The reopened Session, or None if validation failed.
        """
        session = await self._validate_reopen_preconditions(
            session_id, skip_age_check=skip_age_check
        )
        if session is None:
            return None

        now = datetime.now(UTC)

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

    async def can_reopen_session(
        self, session_id: str, *, skip_age_check: bool = False
    ) -> bool:
        """Check whether a session can be reopened, without side effects.

        Validates the same preconditions as ``reopen_session`` but performs
        no state changes. Returns True if the session passes all checks.
        """
        try:
            return await self._validate_reopen_preconditions(
                session_id, skip_age_check=skip_age_check
            ) is not None
        except Exception as exc:
            _log.warning(
                "can_reopen_session error: session_id={id} err={err}",
                id=session_id,
                err=str(exc),
            )
            return False

    async def get_recent_closed(self, before: datetime, window: timedelta) -> list[Session]:
        """Return recently closed sessions that are valid for resumption.

        Delegates to repository for the DB query, then filters to only include
        sessions whose transcript exists locally and whose started_at is within
        the configured max age. Used by coordinator to find resumption candidates.

        Args:
            before: The reference timestamp (typically now).
            window: How far back to look for closed sessions.
        """
        recent = await self._repository.get_recent_closed(before, window)
        cutoff = before - self._max_session_age

        return [
            s
            for s in recent
            if s.transcript_path is not None
            and Path(s.transcript_path).exists()
            and s.started_at > cutoff
        ]

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

    async def save_context_entries(
        self, session_id: str, entries: list[tuple[str, str, dict | None]]
    ) -> list[SessionContextEntry]:
        """Save context entries for a session.

        Best-effort persistence: failures are logged but not raised.
        This ensures context persistence failures don't interrupt conversations.

        Args:
            session_id: The session to save entries for.
            entries: List of (owner, content, metadata) tuples to persist.

        Returns:
            List of persisted SessionContextEntry instances, or empty list on failure.
        """
        if not entries:
            return []

        try:
            saved = await self._repository.save_context_entries(session_id, entries)
            _log.debug(
                "Context entries saved: session_id={id} count={count}",
                id=session_id,
                count=len(entries),
            )
            return saved
        except Exception as exc:
            _log.warning(
                "Failed to save context entries (best-effort): session_id={id} err={err}",
                id=session_id,
                err=str(exc),
            )
            return []

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

    async def update_context_entry(
        self,
        entry_id: int,
        *,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> SessionContextEntry | None:
        """Update a single context entry's content and/or metadata.

        Best-effort: failures are logged and ``None`` is returned. A ``None``
        return also occurs when the entry no longer exists (benign race).

        Args:
            entry_id: The autoincrement id of the context entry.
            content: New content string (prepended notice, etc.), or ``None``
                to leave unchanged.
            metadata: New metadata dict (merged by caller), or ``None`` to
                leave unchanged.

        Returns:
            The refreshed entry, or ``None`` on failure / not-found.
        """
        try:
            return await self._repository.update_context_entry(
                entry_id, content=content, metadata=metadata
            )
        except Exception as exc:
            _log.warning(
                "Failed to update context entry (best-effort): entry_id={id} err={err}",
                id=entry_id,
                err=str(exc),
            )
            return None

    async def find_context_entries_by_skill_name(
        self,
        session_id: str,
        skill_names: list[str],
    ) -> list[SessionContextEntry]:
        """Find context entries whose ``metadata.skill_name`` matches any of *skill_names*.

        Delegates to repository. Returns entries ordered by id ascending.
        Failures are logged and an empty list is returned (best-effort).
        """
        try:
            return await self._repository.find_context_entries_by_skill_name(
                session_id, skill_names
            )
        except Exception as exc:
            _log.warning(
                "Failed to find context entries by skill name (best-effort): "
                "session_id={id} err={err}",
                id=session_id,
                err=str(exc),
            )
            return []

    # ------------------------------------------------------------------
    # Channel messages
    # ------------------------------------------------------------------

    async def record_channel_message(
        self, session_id: str, channel: str, direction: MessageDirection, external_id: str
    ) -> None:
        """Record a channel-specific message ID mapping to a session.

        Best-effort: failures are logged but not raised.
        Returns early with a warning if no active session.

        Args:
            session_id: The session to associate the message with.
            channel: The channel identifier (e.g., "telegram").
            direction: "incoming" or "outgoing" (MessageDirection).
            external_id: The platform-specific message ID.
        """
        if self._active_session is None:
            _log.warning(
                "Cannot record channel message: no active session channel={ch} external_id={eid}",
                ch=channel,
                eid=external_id,
            )
            return

        try:
            message = ChannelMessage(
                session_id=session_id,
                channel=channel,
                direction=direction,
                external_id=external_id,
            )
            await self._repository.save_channel_message(message)

            _log.debug(
                "Channel message recorded: session_id={sid} channel={ch} "
                "direction={dir} external_id={eid}",
                sid=session_id,
                ch=channel,
                dir=direction,
                eid=external_id,
            )
        except Exception as exc:
            _log.warning(
                "Failed to record channel message (best-effort): "
                "session_id={sid} channel={ch} external_id={eid} err={err}",
                sid=session_id,
                ch=channel,
                eid=external_id,
                err=str(exc),
            )

    async def find_session_by_external_id(self, channel: str, external_id: str) -> str | None:
        """Look up the session_id for a (channel, external_id) pair.

        Best-effort: failures are logged and None is returned.
        Unlike record_channel_message, this does not require an active session
        since it is a pure lookup that does not depend on session state.

        Args:
            channel: The channel identifier (e.g., "telegram").
            external_id: The platform-specific message ID.

        Returns:
            The session_id if found, or None.
        """
        try:
            return await self._repository.lookup_session(channel, external_id)
        except Exception as exc:
            _log.warning(
                "Failed to find session by external ID (best-effort): "
                "channel={ch} external_id={eid} err={err}",
                ch=channel,
                eid=external_id,
                err=str(exc),
            )
            return None

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
