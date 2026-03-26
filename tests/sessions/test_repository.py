"""Integration tests for SessionRepository.

Tests for DLT-027: Track conversation sessions.
Uses real SQLite databases in tmp_path (no mocking of the DB layer).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tachikoma.database import Database
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
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield SessionRepository(database.session_factory)
    await database.close()


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

        s1 = _make_session("s1", started_at=base + timedelta(hours=1))
        s2 = _make_session("s2", started_at=base - timedelta(hours=1))

        await repo.create(s1)
        await repo.create(s2)
        await repo.update("s1", ended_at=base + timedelta(hours=2))
        await repo.update("s2", ended_at=base + timedelta(hours=1))

        results = await repo.get_by_time_range(base, base + timedelta(hours=3))
        result_ids = {s.id for s in results}

        assert "s1" in result_ids
        assert "s2" in result_ids

    async def test_excludes_sessions_outside_range(self, repo: SessionRepository) -> None:
        """AC: sessions entirely outside the range are not returned."""
        base = _utcnow()

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


class TestRepositoryContextEntries:
    """Tests for context entry persistence (DLT-041)."""

    async def test_save_context_entries_returns_entries_with_ids(
        self, repo: SessionRepository
    ) -> None:
        """AC: save_context_entries returns entries with autoincrement ids."""
        session = _make_session("ctx-test-1")
        await repo.create(session)

        entries = await repo.save_context_entries(
            "ctx-test-1",
            [
                ("foundational", "<soul>Content</soul>"),
                ("memories", "<memories>User likes Python</memories>"),
            ],
        )

        assert len(entries) == 2
        assert all(e.id > 0 for e in entries)
        assert entries[0].session_id == "ctx-test-1"
        assert entries[0].owner == "foundational"
        assert entries[1].owner == "memories"

    async def test_save_context_entries_empty_list_returns_empty(
        self, repo: SessionRepository
    ) -> None:
        """AC: saving an empty list returns empty list (no-op)."""
        session = _make_session("ctx-test-2")
        await repo.create(session)

        entries = await repo.save_context_entries("ctx-test-2", [])

        assert entries == []

    async def test_load_context_entries_returns_saved_entries(
        self, repo: SessionRepository
    ) -> None:
        """AC: load_context_entries returns entries in insertion order."""
        session = _make_session("ctx-test-3")
        await repo.create(session)

        await repo.save_context_entries(
            "ctx-test-3",
            [
                ("foundational", "First entry"),
                ("memories", "Second entry"),
                ("skills", "Third entry"),
            ],
        )

        loaded = await repo.load_context_entries("ctx-test-3")

        assert len(loaded) == 3
        # Order preserved by autoincrement id
        assert loaded[0].owner == "foundational"
        assert loaded[1].owner == "memories"
        assert loaded[2].owner == "skills"

    async def test_load_context_entries_empty_for_nonexistent_session(
        self, repo: SessionRepository
    ) -> None:
        """AC: loading entries for nonexistent session returns empty list."""
        loaded = await repo.load_context_entries("nonexistent-session")

        assert loaded == []

    async def test_load_context_entries_empty_when_no_entries_saved(
        self, repo: SessionRepository
    ) -> None:
        """AC: loading entries for session with no entries returns empty list."""
        session = _make_session("ctx-test-4")
        await repo.create(session)

        loaded = await repo.load_context_entries("ctx-test-4")

        assert loaded == []

    async def test_entries_isolated_by_session(
        self, repo: SessionRepository
    ) -> None:
        """AC: entries from one session don't leak to another."""
        s1 = _make_session("session-1")
        s2 = _make_session("session-2")
        await repo.create(s1)
        await repo.create(s2)

        await repo.save_context_entries(
            "session-1",
            [("owner-a", "Content for session 1")],
        )
        await repo.save_context_entries(
            "session-2",
            [("owner-b", "Content for session 2")],
        )

        loaded1 = await repo.load_context_entries("session-1")
        loaded2 = await repo.load_context_entries("session-2")

        assert len(loaded1) == 1
        assert loaded1[0].owner == "owner-a"
        assert len(loaded2) == 1
        assert loaded2[0].owner == "owner-b"

    async def test_entry_content_preserved_exactly(
        self, repo: SessionRepository
    ) -> None:
        """AC: stored content matches the text at injection time (content integrity)."""
        session = _make_session("ctx-test-5")
        await repo.create(session)

        original_content = """<memories>
User prefers:
- Dark mode
- Vim keybindings
- Python over JavaScript
</memories>"""

        await repo.save_context_entries(
            "ctx-test-5",
            [("memories", original_content)],
        )

        loaded = await repo.load_context_entries("ctx-test-5")

        assert loaded[0].content == original_content
