"""Integration tests for SessionRepository.

Tests for DLT-027: Track conversation sessions.
Uses real SQLite databases in tmp_path (no mocking of the DB layer).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
import pytest

from tachikoma.sessions.errors import SessionRepositoryError
from tachikoma.sessions.model import Session
from tachikoma.sessions.repository import SessionRepository


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _make_session(
    session_id: str = "test-id",
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    sdk_session_id: str | None = None,
    transcript_path: str | None = None,
    summary: str | None = None,
) -> Session:
    return Session(
        id=session_id,
        started_at=started_at or _utcnow(),
        ended_at=ended_at,
        sdk_session_id=sdk_session_id,
        transcript_path=transcript_path,
        summary=summary,
    )


@pytest.fixture
async def repo(tmp_path: Path) -> SessionRepository:
    """Initialized SessionRepository backed by a temp SQLite file."""
    repository = SessionRepository(tmp_path / "sessions.db")
    await repository.initialize()
    yield repository
    await repository.close()


class TestRepositoryInitialization:
    """Tests for schema auto-creation and engine lifecycle."""

    async def test_creates_database_file_on_initialize(self, tmp_path: Path) -> None:
        """AC: database file does not exist → created on initialize()."""
        db_path = tmp_path / "sessions.db"
        assert not db_path.exists()

        repo = SessionRepository(db_path)
        await repo.initialize()
        await repo.close()

        assert db_path.exists()

    async def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        """Schema creation twice raises no errors (create_all is idempotent)."""
        db_path = tmp_path / "sessions.db"
        repo = SessionRepository(db_path)

        await repo.initialize()
        await repo.close()

        # Second initialization on same DB should succeed
        repo2 = SessionRepository(db_path)
        await repo2.initialize()
        await repo2.close()

    async def test_requires_initialization_before_operations(self, tmp_path: Path) -> None:
        """AC: operations before initialize() raise SessionRepositoryError."""
        repo = SessionRepository(tmp_path / "sessions.db")

        with pytest.raises(SessionRepositoryError, match="not initialized"):
            await repo.get_open_sessions()


class TestRepositoryCreate:
    """Tests for session creation."""

    async def test_create_and_retrieve_by_id(self, repo: SessionRepository) -> None:
        """AC: create then get_by_id returns the session."""
        session = _make_session("s1")
        await repo.create(session)

        retrieved = await repo.get_by_id("s1")

        assert retrieved is not None
        assert retrieved.id == "s1"

    async def test_create_preserves_all_fields(self, repo: SessionRepository) -> None:
        """AC: all fields round-trip through the database."""
        now = _utcnow()
        session = _make_session(
            "s2",
            started_at=now,
            sdk_session_id="sdk-abc",
            transcript_path="/path/to/t.jsonl",
        )
        await repo.create(session)

        retrieved = await repo.get_by_id("s2")

        assert retrieved is not None
        assert retrieved.sdk_session_id == "sdk-abc"
        assert retrieved.transcript_path == "/path/to/t.jsonl"
        assert retrieved.started_at == now

    async def test_created_session_has_null_ended_at(self, repo: SessionRepository) -> None:
        """AC: newly created sessions are open (ended_at is None)."""
        session = _make_session("s3")
        await repo.create(session)

        retrieved = await repo.get_by_id("s3")

        assert retrieved is not None
        assert retrieved.ended_at is None
        assert retrieved.status == "open"


class TestRepositoryGetById:
    """Tests for get_by_id queries."""

    async def test_returns_none_for_unknown_id(self, repo: SessionRepository) -> None:
        """AC: get_by_id returns None for missing session (not an error)."""
        result = await repo.get_by_id("nonexistent")

        assert result is None


class TestRepositoryUpdate:
    """Tests for session field updates."""

    async def test_update_ended_at_closes_session(self, repo: SessionRepository) -> None:
        """AC: updating ended_at marks the session as closed."""
        session = _make_session("s4")
        await repo.create(session)

        end_time = _utcnow()
        await repo.update("s4", ended_at=end_time)

        retrieved = await repo.get_by_id("s4")
        assert retrieved is not None
        assert retrieved.ended_at == end_time

    async def test_update_sdk_session_id_and_transcript_path(self, repo: SessionRepository) -> None:
        """AC: sdk_session_id and transcript_path can be updated."""
        session = _make_session("s5")
        await repo.create(session)

        await repo.update("s5", sdk_session_id="sdk-xyz", transcript_path="/new/path.jsonl")

        retrieved = await repo.get_by_id("s5")
        assert retrieved is not None
        assert retrieved.sdk_session_id == "sdk-xyz"
        assert retrieved.transcript_path == "/new/path.jsonl"

    async def test_update_nonexistent_id_is_noop(self, repo: SessionRepository) -> None:
        """AC: updating an ID that doesn't exist raises no error."""
        await repo.update("ghost", ended_at=_utcnow())

    async def test_update_summary_field(self, repo: SessionRepository) -> None:
        """AC: summary field can be updated."""
        session = _make_session("s6")
        await repo.create(session)

        await repo.update("s6", summary="Updated conversation summary")

        retrieved = await repo.get_by_id("s6")
        assert retrieved is not None
        assert retrieved.summary == "Updated conversation summary"


class TestRepositoryGetOpenSessions:
    """Tests for get_open_sessions."""

    async def test_returns_only_open_sessions(self, repo: SessionRepository) -> None:
        """AC: get_open_sessions returns only sessions with null ended_at."""
        now = _utcnow()

        await repo.create(_make_session("open1", started_at=now))
        await repo.create(_make_session("open2", started_at=now))
        await repo.create(_make_session("closed1", started_at=now))

        await repo.update("closed1", ended_at=now)

        open_sessions = await repo.get_open_sessions()
        open_ids = {s.id for s in open_sessions}

        assert "open1" in open_ids
        assert "open2" in open_ids
        assert "closed1" not in open_ids

    async def test_returns_empty_when_no_open_sessions(self, repo: SessionRepository) -> None:
        """AC: empty result when no open sessions exist."""
        result = await repo.get_open_sessions()

        assert result == []


class TestRepositoryGetByTimeRange:
    """Tests for get_by_time_range queries."""

    async def test_returns_sessions_overlapping_range(self, repo: SessionRepository) -> None:
        """AC: sessions whose span overlaps the query range are returned."""
        base = _utcnow()

        # Session that starts and ends within the range
        s1 = _make_session("s1", started_at=base + timedelta(hours=1))
        # Session that starts before and ends within the range
        s2 = _make_session("s2", started_at=base - timedelta(hours=1))

        await repo.create(s1)
        await repo.create(s2)
        # Close them via update to persist ended_at
        await repo.update("s1", ended_at=base + timedelta(hours=2))
        await repo.update("s2", ended_at=base + timedelta(hours=1))

        results = await repo.get_by_time_range(base, base + timedelta(hours=3))
        result_ids = {s.id for s in results}

        assert "s1" in result_ids
        assert "s2" in result_ids

    async def test_excludes_sessions_outside_range(self, repo: SessionRepository) -> None:
        """AC: sessions entirely outside the range are not returned."""
        base = _utcnow()

        # Session that ended before the range starts
        past = _make_session("past", started_at=base - timedelta(hours=5))
        await repo.create(past)
        await repo.update("past", ended_at=base - timedelta(hours=3))

        results = await repo.get_by_time_range(base, base + timedelta(hours=1))
        result_ids = {s.id for s in results}

        assert "past" not in result_ids

    async def test_includes_open_sessions_started_before_range_end(
        self, repo: SessionRepository
    ) -> None:
        """AC: open sessions (null ended_at) are included if started_at < range_end."""
        base = _utcnow()

        # Open session started in the middle of the range
        open_session = _make_session("open1", started_at=base + timedelta(hours=1))
        await repo.create(open_session)

        results = await repo.get_by_time_range(base, base + timedelta(hours=3))
        result_ids = {s.id for s in results}

        assert "open1" in result_ids

    async def test_returns_empty_for_no_overlap(self, repo: SessionRepository) -> None:
        """AC: empty result when no sessions overlap the range."""
        base = _utcnow()

        results = await repo.get_by_time_range(base, base + timedelta(hours=1))

        assert results == []

    async def test_ordered_by_started_at_descending(self, repo: SessionRepository) -> None:
        """AC: results are ordered by started_at descending."""
        base = _utcnow()

        s1 = _make_session("earlier", started_at=base)
        s2 = _make_session("later", started_at=base + timedelta(hours=1))
        await repo.create(s1)
        await repo.create(s2)

        results = await repo.get_by_time_range(
            base - timedelta(minutes=1), base + timedelta(hours=2)
        )

        assert results[0].id == "later"
        assert results[1].id == "earlier"


class TestRepositoryClose:
    """Tests for engine disposal."""

    async def test_close_disposes_engine(self, tmp_path: Path) -> None:
        """AC: close() disposes the engine without error."""
        repo = SessionRepository(tmp_path / "sessions.db")
        await repo.initialize()

        await repo.close()

        # After close, further operations should raise
        with pytest.raises(SessionRepositoryError):
            await repo.get_open_sessions()


class TestRepositoryMigration:
    """Tests for Alembic schema migrations."""

    async def test_initialize_creates_full_schema(self, tmp_path: Path) -> None:
        """AC: initialize() creates the sessions table with all expected columns."""
        db_path = tmp_path / "sessions.db"

        repo = SessionRepository(db_path)
        await repo.initialize()
        await repo.close()

        # Verify all expected columns exist
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('sessions')")
            columns = {row[1] for row in await cursor.fetchall()}

        expected_columns = {
            "id",
            "sdk_session_id",
            "transcript_path",
            "summary",
            "started_at",
            "ended_at",
            "last_resumed_at",
        }
        assert expected_columns.issubset(columns)

    async def test_initialize_migrates_existing_database(
        self, tmp_path: Path
    ) -> None:
        """AC: initialize() adds new columns to existing database via Alembic."""
        db_path = tmp_path / "sessions.db"

        # Create a table with the OLD schema (no last_resumed_at)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    sdk_session_id TEXT,
                    transcript_path TEXT,
                    summary TEXT,
                    started_at TEXT,
                    ended_at TEXT
                )"""
            )
            # Add alembic_version to mark the old baseline as applied
            await db.execute(
                """CREATE TABLE alembic_version (
                    version_num TEXT PRIMARY KEY
                )"""
            )
            await db.execute(
                "INSERT INTO alembic_version (version_num) VALUES ('001_initial')"
            )
            await db.commit()

        # Verify last_resumed_at column doesn't exist
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM pragma_table_info('sessions') WHERE name='last_resumed_at'"
            )
            row = await cursor.fetchone()
            assert row is None

        # Initialize should run pending migrations
        repo = SessionRepository(db_path)
        await repo.initialize()
        await repo.close()

        # Verify the column was added
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM pragma_table_info('sessions') WHERE name='last_resumed_at'"
            )
            row = await cursor.fetchone()
            assert row is not None

    async def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        """AC: calling initialize() multiple times is safe (Alembic upgrade head)."""
        db_path = tmp_path / "sessions.db"

        repo = SessionRepository(db_path)
        await repo.initialize()

        # Second initialization should succeed without error
        await repo.close()
        repo2 = SessionRepository(db_path)
        await repo2.initialize()
        await repo2.close()

