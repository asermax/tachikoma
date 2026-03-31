"""Unit tests for Session domain model.

Tests for DLT-027: Track conversation sessions.
Tests for DLT-041: Persist session context to database.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from tachikoma.sessions.model import Session, SessionContextEntry


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestSessionStatus:
    """Tests for Session.status computed property."""

    def test_open_when_ended_at_is_none(self) -> None:
        """AC: ended_at is None → status is 'open'."""
        session = Session(id="abc", started_at=_utcnow())

        assert session.status == "open"

    def test_closed_when_both_ended_at_and_sdk_session_id_set(self) -> None:
        """AC: ended_at set + sdk_session_id set → status is 'closed'."""
        session = Session(
            id="abc",
            started_at=_utcnow(),
            ended_at=_utcnow(),
            sdk_session_id="sdk-123",
        )

        assert session.status == "closed"

    def test_interrupted_when_ended_at_set_but_no_sdk_session_id(self) -> None:
        """AC: ended_at set + sdk_session_id None → status is 'interrupted'."""
        session = Session(
            id="abc",
            started_at=_utcnow(),
            ended_at=_utcnow(),
            sdk_session_id=None,
        )

        assert session.status == "interrupted"


class TestSessionDataclass:
    """Tests for Session dataclass behavior."""

    def test_is_frozen(self) -> None:
        """Session is a frozen dataclass — field assignment raises."""
        session = Session(id="abc", started_at=_utcnow())

        with pytest.raises(FrozenInstanceError):
            session.id = "other"

    def test_optional_fields_default_to_none(self) -> None:
        """Optional fields have None defaults."""
        session = Session(id="abc", started_at=_utcnow())

        assert session.sdk_session_id is None
        assert session.transcript_path is None
        assert session.summary is None
        assert session.ended_at is None

    def test_fields_set_correctly(self) -> None:
        """All fields round-trip correctly."""
        now = _utcnow()
        session = Session(
            id="test-id",
            sdk_session_id="sdk-456",
            transcript_path="/path/to/transcript.jsonl",
            summary="Test conversation summary",
            started_at=now,
            ended_at=now,
        )

        assert session.id == "test-id"
        assert session.sdk_session_id == "sdk-456"
        assert session.transcript_path == "/path/to/transcript.jsonl"
        assert session.summary == "Test conversation summary"
        assert session.started_at == now
        assert session.ended_at == now


class TestSessionSummary:
    """Tests for Session.summary field."""

    def test_summary_defaults_to_none(self) -> None:
        """AC: summary field defaults to None."""
        session = Session(id="abc", started_at=_utcnow())

        assert session.summary is None

    def test_summary_field_set_correctly(self) -> None:
        """AC: summary field round-trips correctly."""
        session = Session(
            id="abc",
            started_at=_utcnow(),
            summary="User discussed Python testing frameworks.",
        )

        assert session.summary == "User discussed Python testing frameworks."


class TestSessionContextEntry:
    """Tests for SessionContextEntry dataclass (DLT-041)."""

    def test_is_frozen(self) -> None:
        """SessionContextEntry is a frozen dataclass — field assignment raises."""
        entry = SessionContextEntry(
            id=1,
            session_id="session-abc",
            owner="memories",
            content="User likes Python",
        )

        with pytest.raises(FrozenInstanceError):
            entry.content = "modified"

    def test_fields_set_correctly(self) -> None:
        """All fields round-trip correctly."""
        entry = SessionContextEntry(
            id=42,
            session_id="test-session-id",
            owner="foundational",
            content="<soul>\nYou are helpful\n</soul>",
        )

        assert entry.id == 42
        assert entry.session_id == "test-session-id"
        assert entry.owner == "foundational"
        assert entry.content == "<soul>\nYou are helpful\n</soul>"

    def test_required_fields_have_no_defaults(self) -> None:
        """All fields are required — no defaults."""
        with pytest.raises(TypeError):
            SessionContextEntry()

        with pytest.raises(TypeError):
            SessionContextEntry(id=1, session_id="abc", owner="test")

    def test_entry_captures_context_at_injection_time(self) -> None:
        """AC: Entry content matches what was injected (content integrity)."""
        original_content = "<memories>\nUser prefers dark mode\n</memories>"
        entry = SessionContextEntry(
            id=1,
            session_id="session-123",
            owner="memories",
            content=original_content,
        )

        # Content is preserved exactly
        assert entry.content == original_content
