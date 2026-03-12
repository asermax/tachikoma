"""SessionRepository: async SQLAlchemy persistence layer for conversation sessions.

Owns the AsyncEngine lifecycle. All callers receive Session dataclasses —
SQLAlchemy types never leak out of this module.
"""

from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import Base, Session, SessionRecord

_log = logger.bind(component="sessions")


class SessionRepository:
    """Async repository for conversation sessions backed by SQLite via aiosqlite.

    Usage::

        repo = SessionRepository(data_path / "sessions.db")
        await repo.initialize()
        try:
            session = await repo.create(session_obj)
            ...
        finally:
            await repo.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    async def initialize(self) -> None:
        """Create the async engine, session factory, and database schema.

        Idempotent: calling multiple times is safe.
        """
        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, echo=False)

        # expire_on_commit=False lets us access attributes after commit without refresh
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        # Base.metadata.create_all is synchronous; bridge into the async engine via run_sync
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        _log.info("Session repository initialized: db_path={path}", path=self._db_path)

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def create(self, session: Session) -> Session:
        """Persist a new session and return it."""
        self._require_initialized()

        try:
            record = SessionRecord(
                id=session.id,
                sdk_session_id=session.sdk_session_id,
                transcript_path=session.transcript_path,
                started_at=session.started_at,
                ended_at=session.ended_at,
            )

            async with self._session_factory() as db:  # type: ignore[misc]
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise SessionRepositoryError(f"Failed to create session {session.id}") from exc

    async def update(self, session_id: str, **fields) -> None:
        """Update arbitrary fields on a session by ID.

        Accepted fields: sdk_session_id, transcript_path, ended_at.
        """
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
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
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
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
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                stmt = (
                    select(SessionRecord)
                    .where(
                        SessionRecord.started_at < end,
                        (SessionRecord.ended_at.is_(None))
                        | (SessionRecord.ended_at > start),
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
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
                stmt = select(SessionRecord).where(SessionRecord.ended_at.is_(None))
                result = await db.execute(stmt)
                records = result.scalars().all()

            return [r.to_domain() for r in records]

        except Exception as exc:
            raise SessionRepositoryError("Failed to get open sessions") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_initialized(self) -> None:
        if self._engine is None or self._session_factory is None:
            raise SessionRepositoryError(
                "SessionRepository is not initialized. Call initialize() first."
            )
