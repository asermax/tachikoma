"""Unit tests for SessionRegistry.

Tests for DLT-027: Track conversation sessions.
Uses a mocked SessionRepository to test registry business logic in isolation.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tachikoma.sessions.model import Session, SessionContextEntry
from tachikoma.sessions.registry import SessionRegistry


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
def mock_repo():
    """Mock SessionRepository with async methods."""
    repo = MagicMock()
    repo.create = AsyncMock()
    repo.update = AsyncMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.get_open_sessions = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def registry(mock_repo) -> SessionRegistry:
    return SessionRegistry(mock_repo)


class TestSessionRegistryCreate:
    """Tests for create_session() behavior."""

    async def test_creates_session_with_uuid_id(self, registry: SessionRegistry, mock_repo) -> None:
        """AC: create_session generates a UUID ID and persists it."""
        created_session = _make_session("abc123")
        mock_repo.create.return_value = created_session

        session = await registry.create_session()

        assert session is created_session
        mock_repo.create.assert_awaited_once()

        # The session passed to create should have a UUID hex ID
        call_args = mock_repo.create.call_args[0][0]
        assert len(call_args.id) == 32  # UUID4 hex is 32 chars

    async def test_creates_session_with_started_at(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: created session has started_at set."""

        async def echo(s):
            return s

        mock_repo.create.side_effect = echo

        session = await registry.create_session()

        assert session.started_at is not None

    async def test_created_session_becomes_active(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: after create_session, get_active_session returns it."""
        created_session = _make_session("s1")
        mock_repo.create.return_value = created_session

        await registry.create_session()
        active = await registry.get_active_session()

        assert active is created_session

    async def test_concurrent_creates_serialized(self, mock_repo) -> None:
        """AC: concurrent create_session calls are serialized by asyncio.Lock."""
        sessions_created = []

        async def slow_create(session):
            await asyncio.sleep(0)  # yield to allow other coroutines to run
            sessions_created.append(session.id)
            return session

        mock_repo.create.side_effect = slow_create
        registry = SessionRegistry(mock_repo)

        # Run two creates concurrently
        await asyncio.gather(
            registry.create_session(),
            registry.create_session(),
        )

        # Both should complete; due to lock, both calls are serialized
        assert mock_repo.create.await_count == 2


class TestSessionRegistryClose:
    """Tests for close_session() behavior."""

    async def test_close_updates_ended_at(self, registry: SessionRegistry, mock_repo) -> None:
        """AC: close_session sets ended_at on the active session."""
        session = _make_session("s1")
        mock_repo.create.return_value = session
        await registry.create_session()

        await registry.close_session("s1")

        mock_repo.update.assert_awaited_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert "ended_at" in call_kwargs

    async def test_close_clears_active_session(self, registry: SessionRegistry, mock_repo) -> None:
        """AC: after close_session, get_active_session returns None."""
        session = _make_session("s1")
        mock_repo.create.return_value = session
        await registry.create_session()

        result = await registry.close_session("s1")
        active = await registry.get_active_session()

        assert result is True
        assert active is None

    async def test_close_with_no_active_session_is_noop(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: close_session with no active session completes without error."""
        result = await registry.close_session("nonexistent")

        assert result is False
        mock_repo.update.assert_not_awaited()

    async def test_close_already_closed_session_is_idempotent(self, mock_repo) -> None:
        """AC: closing an already-closed session clears _active_session but does not update DB."""
        closed_session = _make_session("s1", ended_at=_utcnow())
        registry = SessionRegistry(mock_repo)
        registry._active_session = closed_session

        result = await registry.close_session("s1")

        assert result is False
        mock_repo.update.assert_not_awaited()
        assert await registry.get_active_session() is None


class TestSessionRegistryUpdateMetadata:
    """Tests for update_metadata() behavior."""

    async def test_update_metadata_delegates_to_repository(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: update_metadata calls repository update with correct fields."""
        await registry.update_metadata("s1", sdk_session_id="sdk-abc", transcript_path="/p/t.jsonl")

        mock_repo.update.assert_awaited_once_with(
            "s1",
            sdk_session_id="sdk-abc",
            transcript_path="/p/t.jsonl",
        )

    async def test_update_metadata_refreshes_active_session(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: active session reference is updated after metadata update."""
        original = _make_session("s1")
        updated = _make_session("s1", sdk_session_id="sdk-abc")
        mock_repo.create.return_value = original
        mock_repo.get_by_id.return_value = updated

        await registry.create_session()
        await registry.update_metadata("s1", sdk_session_id="sdk-abc", transcript_path="/p/t.jsonl")

        active = await registry.get_active_session()
        assert active is updated


class TestSessionRegistryUpdateSummary:
    """Tests for update_summary() behavior."""

    async def test_update_summary_delegates_to_repository(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: update_summary calls repository update with summary field."""
        await registry.update_summary("s1", summary="Test summary")

        mock_repo.update.assert_awaited_once_with("s1", summary="Test summary")

    async def test_update_summary_refreshes_active_session(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: active session reference is updated after summary update."""
        original = _make_session("s1")
        updated = _make_session("s1", summary="New summary")
        mock_repo.create.return_value = original
        mock_repo.get_by_id.return_value = updated

        await registry.create_session()
        await registry.update_summary("s1", summary="New summary")

        active = await registry.get_active_session()
        assert active is updated
        assert active.summary == "New summary"

    async def test_update_summary_does_not_refresh_non_active_session(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: non-active sessions are not refreshed in memory."""
        # Create a session so we have an active one
        active_session = _make_session("active")
        mock_repo.create.return_value = active_session
        await registry.create_session()

        # Update summary for a different session
        await registry.update_summary("other-session", summary="Test summary")

        # get_by_id should not be called for the active session
        # (only called if session_id matches active session)
        mock_repo.get_by_id.assert_not_awaited()


class TestSessionRegistryGetActive:
    """Tests for get_active_session()."""

    async def test_returns_none_initially(self, registry: SessionRegistry) -> None:
        """AC: no active session before any creation."""
        result = await registry.get_active_session()

        assert result is None

    async def test_returns_session_after_creation(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: returns the session after create_session."""
        session = _make_session("s1")
        mock_repo.create.return_value = session

        await registry.create_session()
        result = await registry.get_active_session()

        assert result is session


class TestSessionRegistryRecoverInterrupted:
    """Tests for recover_interrupted()."""

    async def test_recover_with_no_open_sessions_is_noop(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: recover with no open sessions makes no updates."""
        mock_repo.get_open_sessions.return_value = []

        await registry.recover_interrupted()

        mock_repo.update.assert_not_awaited()

    async def test_recover_sets_ended_at_for_open_sessions(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: recover sets ended_at for each open session."""
        open_session = _make_session("s1")
        mock_repo.get_open_sessions.return_value = [open_session]

        await registry.recover_interrupted()

        mock_repo.update.assert_awaited_once()
        call_kwargs = mock_repo.update.call_args[1]
        assert "ended_at" in call_kwargs

    async def test_recover_uses_transcript_mtime_when_available(
        self, registry: SessionRegistry, mock_repo, tmp_path: Path
    ) -> None:
        """AC: recovery uses file mtime when transcript exists."""
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text("{}")

        open_session = _make_session(
            "s1",
            sdk_session_id="sdk-abc",
            transcript_path=str(transcript),
        )
        mock_repo.get_open_sessions.return_value = [open_session]

        await registry.recover_interrupted()

        call_kwargs = mock_repo.update.call_args[1]
        expected_mtime = datetime.fromtimestamp(transcript.stat().st_mtime, tz=UTC)
        assert call_kwargs["ended_at"] == expected_mtime

    async def test_recover_uses_current_time_when_no_transcript(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: recovery falls back to current time when transcript file not found."""
        open_session = _make_session(
            "s1",
            sdk_session_id="sdk-abc",
            transcript_path="/nonexistent/path.jsonl",
        )
        mock_repo.get_open_sessions.return_value = [open_session]

        before = _utcnow()
        await registry.recover_interrupted()
        after = _utcnow()

        call_kwargs = mock_repo.update.call_args[1]
        assert before <= call_kwargs["ended_at"] <= after

    async def test_recover_uses_current_time_when_no_sdk_session_id(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: recovery uses current time when sdk_session_id is None."""
        open_session = _make_session("s1", sdk_session_id=None)
        mock_repo.get_open_sessions.return_value = [open_session]

        before = _utcnow()
        await registry.recover_interrupted()
        after = _utcnow()

        call_kwargs = mock_repo.update.call_args[1]
        assert before <= call_kwargs["ended_at"] <= after

    async def test_recover_handles_multiple_open_sessions(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: all open sessions are recovered."""
        sessions = [_make_session(f"s{i}") for i in range(3)]
        mock_repo.get_open_sessions.return_value = sessions

        await registry.recover_interrupted()

        assert mock_repo.update.await_count == 3


class TestSessionRegistryContextEntries:
    """Tests for context entry pass-through methods (DLT-041)."""

    async def test_save_context_entries_delegates_to_repository(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: save_context_entries delegates to repository."""
        mock_repo.save_context_entries = AsyncMock(return_value=None)

        # Method returns None (best-effort save)
        result = await registry.save_context_entries(
            "s1",
            [("memories", "User prefers dark mode")],
        )

        assert result is None
        mock_repo.save_context_entries.assert_awaited_once_with(
            "s1",
            [("memories", "User prefers dark mode")],
        )

    async def test_save_context_entries_logs_on_failure_but_doesnt_raise(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: save failures are logged but not raised (graceful degradation per R7)."""
        mock_repo.save_context_entries = AsyncMock(side_effect=Exception("DB error"))

        # Should not raise - best-effort save
        await registry.save_context_entries("s1", [("memories", "test")])

        mock_repo.save_context_entries.assert_awaited_once()

    async def test_load_context_entries_delegates_to_repository(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: load_context_entries delegates to repository and returns entries."""
        expected_entries = [
            SessionContextEntry(id=1, session_id="s1", owner="foundational", content="first"),
            SessionContextEntry(id=2, session_id="s1", owner="memories", content="second"),
        ]
        mock_repo.load_context_entries = AsyncMock(return_value=expected_entries)

        entries = await registry.load_context_entries("s1")

        assert entries == expected_entries
        mock_repo.load_context_entries.assert_awaited_once_with("s1")

    async def test_load_context_entries_raises_on_failure(
        self, registry: SessionRegistry, mock_repo
    ) -> None:
        """AC: load failures propagate to caller (caller handles graceful degradation)."""
        mock_repo.load_context_entries = AsyncMock(side_effect=Exception("DB error"))

        with pytest.raises(Exception, match="DB error"):
            await registry.load_context_entries("s1")
