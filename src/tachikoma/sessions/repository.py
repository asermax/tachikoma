"""SessionRepository: async SQLAlchemy persistence layer for conversation sessions.

All callers receive Session dataclasses — SQLAlchemy types never leak out
of this module.
"""

from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import (
    Session,
    SessionContextEntry,
    SessionContextEntryRecord,
    SessionRecord,
    SessionResumption,
    SessionResumptionRecord,
)

_log = logger.bind(component="sessions")


class SessionRepository:
    """Async repository for conversation sessions backed by SQLite via aiosqlite.

    Receives a shared session factory from the Database class.

    Usage::

        repo = SessionRepository(database.session_factory)
        session = await repo.create(session_obj)
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def create(self, session: Session) -> Session:
        """Persist a new session and return it."""
        try:
            record = SessionRecord(
                id=session.id,
                sdk_session_id=session.sdk_session_id,
                transcript_path=session.transcript_path,
                summary=session.summary,
                started_at=session.started_at,
                ended_at=session.ended_at,
                last_resumed_at=session.last_resumed_at,
                processed_at=session.processed_at,
            )

            async with self._session_factory() as db:
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise SessionRepositoryError(f"Failed to create session {session.id}") from exc

    async def update(self, session_id: str, **fields) -> None:
        """Update arbitrary fields on a session by ID.

        Accepted fields: sdk_session_id, transcript_path, summary, ended_at.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(SessionRecord).where(SessionRecord.id == session_id)
                )
                record = result.scalar_one_or_none()

                if record is None:
                    return

                for key, value in fields.items():
                    setattr(record, key, value)

                await db.commit()

        except Exception as exc:
            raise SessionRepositoryError(f"Failed to update session {session_id}") from exc

    async def get_by_id(self, session_id: str) -> Session | None:
        """Return the session with the given ID, or None if not found."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(SessionRecord).where(SessionRecord.id == session_id)
                )
                record = result.scalar_one_or_none()

            return record.to_domain() if record is not None else None

        except Exception as exc:
            raise SessionRepositoryError(f"Failed to get session {session_id}") from exc

    async def get_by_time_range(self, start: datetime, end: datetime) -> list[Session]:
        """Return sessions whose time span overlaps the given [start, end) range.

        Sessions with null ended_at are treated as ongoing and included if
        their started_at is before range_end (overlap semantics).
        Results are ordered by started_at descending.
        """
        try:
            async with self._session_factory() as db:
                stmt = (
                    select(SessionRecord)
                    .where(
                        SessionRecord.started_at < end,
                        (SessionRecord.ended_at.is_(None)) | (SessionRecord.ended_at > start),
                    )
                    .order_by(SessionRecord.started_at.desc())
                )
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError("Failed to query sessions by time range") from exc

    async def get_open_sessions(self) -> list[Session]:
        """Return all sessions with null ended_at (open / not yet closed)."""
        try:
            async with self._session_factory() as db:
                stmt = select(SessionRecord).where(SessionRecord.ended_at.is_(None))
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError("Failed to get open sessions") from exc

    async def get_recent_closed(self, before: datetime, window: timedelta) -> list[Session]:
        """Return recently closed sessions within the time window.

        Only returns sessions with:
        - ended_at IS NOT NULL (closed)
        - sdk_session_id IS NOT NULL (can be resumed)
        - summary IS NOT NULL (has topic context for matching)
        - ended_at > (before - window)

        Results are ordered by ended_at descending.

        Args:
            before: The reference timestamp (typically now).
            window: How far back to look for closed sessions.
        """
        try:
            cutoff = before - window
            async with self._session_factory() as db:
                stmt = (
                    select(SessionRecord)
                    .where(
                        SessionRecord.ended_at.is_not(None),
                        SessionRecord.sdk_session_id.is_not(None),
                        SessionRecord.summary.is_not(None),
                        SessionRecord.ended_at > cutoff,
                    )
                    .order_by(SessionRecord.ended_at.desc())
                )
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError("Failed to query recent closed sessions") from exc

    async def create_resumption(self, resumption: SessionResumption) -> SessionResumption:
        """Persist a session resumption event."""
        try:
            record = SessionResumptionRecord(
                session_id=resumption.session_id,
                resumed_at=resumption.resumed_at,
                previous_ended_at=resumption.previous_ended_at,
            )

            async with self._session_factory() as db:
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise SessionRepositoryError(
                f"Failed to create resumption for session {resumption.session_id}"
            ) from exc

    async def get_resumptions_for_session(self, session_id: str) -> list[SessionResumption]:
        """Return all resumption events for a session, ordered by resumed_at ascending."""
        try:
            async with self._session_factory() as db:
                stmt = (
                    select(SessionResumptionRecord)
                    .where(SessionResumptionRecord.session_id == session_id)
                    .order_by(SessionResumptionRecord.resumed_at.asc())
                )
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError(
                f"Failed to get resumptions for session {session_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Context entries
    # ------------------------------------------------------------------

    async def save_context_entries(
        self, session_id: str, entries: list[tuple[str, str]]
    ) -> list[SessionContextEntry]:
        """Persist context entries for a session.

        Bulk-saves all entries in a single transaction. The autoincrement id
        determines assembly order (insertion order).

        Args:
            session_id: The session to associate entries with.
            entries: List of (owner, content) tuples.

        Returns:
            List of persisted SessionContextEntry instances with their ids.

        Raises:
            SessionRepositoryError: If the save operation fails.
        """
        if not entries:
            return []

        try:
            records = [
                SessionContextEntryRecord(
                    session_id=session_id,
                    owner=owner,
                    content=content,
                )
                for owner, content in entries
            ]

            async with self._session_factory() as db:
                db.add_all(records)
                await db.commit()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError(
                f"Failed to save context entries for session {session_id}"
            ) from exc

    async def load_context_entries(self, session_id: str) -> list[SessionContextEntry]:
        """Load all context entries for a session.

        Entries are returned ordered by id ascending (insertion order).

        Args:
            session_id: The session to load entries for.

        Returns:
            List of SessionContextEntry instances, or empty list if none exist.

        Raises:
            SessionRepositoryError: If the load operation fails.
        """
        try:
            async with self._session_factory() as db:
                stmt = (
                    select(SessionContextEntryRecord)
                    .where(SessionContextEntryRecord.session_id == session_id)
                    .order_by(SessionContextEntryRecord.id.asc())
                )
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError(
                f"Failed to load context entries for session {session_id}"
            ) from exc
