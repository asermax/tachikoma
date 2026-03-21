"""SessionRepository: async SQLAlchemy persistence layer for conversation sessions.

Owns the AsyncEngine lifecycle. All callers receive Session dataclasses —
SQLAlchemy types never leak out of this module.
"""

from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.migrations import migrations_path
from tachikoma.sessions.model import (
    Base,
    Session,
    SessionRecord,
    SessionResumption,
    SessionResumptionRecord,
)

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
        """Create the async engine, session factory, and run database migrations.

        Idempotent: calling multiple times is safe.
        Uses Alembic for schema migrations instead of create_all().
        """
        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, echo=False)

        # expire_on_commit=False lets us access attributes after commit without refresh
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        # Run Alembic migrations programmatically
        await self._run_migrations()

        _log.info("Session repository initialized: db_path={path}", path=self._db_path)

    async def _run_migrations(self) -> None:
        """Run schema migrations.

        For SQLite, we use a simple check-and-add approach:
        1. create_all() for fresh databases
        2. Column additions for existing databases
        """
        if self._engine is None:
            return

        # Ensure the database file's parent directory exists
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Use SQLAlchemy's create_all with checkfirst=True for idempotent creation
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Check for and add missing columns (for existing databases)
            # This handles the case where the database was created before DLT-028
            from sqlalchemy import text

            # Check if summary column exists (added in DLT-027)
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='summary'")
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE sessions ADD COLUMN summary TEXT")
                )
                _log.info("Schema migration: added 'summary' column to sessions table")

            # Check if last_resumed_at column exists (added in DLT-028)
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='last_resumed_at'")
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE sessions ADD COLUMN last_resumed_at DATETIME")
                )
                _log.info("Schema migration: added 'last_resumed_at' column to sessions table")

            # Check if session_resumptions table exists (added in DLT-028)
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='session_resumptions'")
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("""
                        CREATE TABLE session_resumptions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL REFERENCES sessions(id),
                            resumed_at DATETIME NOT NULL,
                            previous_ended_at DATETIME NOT NULL
                        )
                    """)
                )
                await conn.execute(
                    text("CREATE INDEX ix_session_resumptions_session_id ON session_resumptions(session_id)")
                )
                _log.info("Schema migration: created 'session_resumptions' table")

        _log.debug("Schema migrations completed: db_path={path}", path=self._db_path)

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
                summary=session.summary,
                started_at=session.started_at,
                ended_at=session.ended_at,
                last_resumed_at=session.last_resumed_at,
            )

            async with self._session_factory() as db:  # type: ignore[misc]
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise SessionRepositoryError(f"Failed to create session {session.id}") from exc

    async def update(self, session_id: str, **fields) -> None:
        """Update arbitrary fields on a session by ID.

        Accepted fields: sdk_session_id, transcript_path, summary, ended_at.
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

    async def get_recent_closed(
        self, before: datetime, window: timedelta
    ) -> list[Session]:
        """Return recently closed sessions within the time window.

        Only returns sessions with:
        - ended_at IS NOT NULL (closed)
        - sdk_session_id IS NOT NULL (can be resumed)
        - ended_at > (before - window)

        Results are ordered by ended_at descending.

        Args:
            before: The reference timestamp (typically now).
            window: How far back to look for closed sessions.
        """
        self._require_initialized()

        try:
            cutoff = before - window
            async with self._session_factory() as db:  # type: ignore[misc]
                stmt = (
                    select(SessionRecord)
                    .where(
                        SessionRecord.ended_at.is_not(None),
                        SessionRecord.sdk_session_id.is_not(None),
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
        self._require_initialized()

        try:
            record = SessionResumptionRecord(
                session_id=resumption.session_id,
                resumed_at=resumption.resumed_at,
                previous_ended_at=resumption.previous_ended_at,
            )

            async with self._session_factory() as db:  # type: ignore[misc]
                db.add(record)
                await db.commit()

            return record.to_domain()

        except Exception as exc:
            raise SessionRepositoryError(
                f"Failed to create resumption for session {resumption.session_id}"
            ) from exc

    async def get_resumptions_for_session(
        self, session_id: str
    ) -> list[SessionResumption]:
        """Return all resumption events for a session, ordered by resumed_at ascending."""
        self._require_initialized()

        try:
            async with self._session_factory() as db:  # type: ignore[misc]
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
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_initialized(self) -> None:
        if self._engine is None or self._session_factory is None:
            raise SessionRepositoryError(
                "SessionRepository is not initialized. Call initialize() first."
            )
