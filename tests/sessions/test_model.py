"""Unit tests for Session domain model.

Tests for DLT-027: Track conversation sessions.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from tachikoma.sessions.model import Session


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
            session.id = "other"  # type: ignore[misc]

    def test_optional_fields_default_to_none(self) -> None:
        """Optional fields have None defaults."""
        session = Session(id="abc", started_at=_utcnow())

        assert session.sdk_session_id is None
        assert session.transcript_path is None
        assert session.ended_at is None

    def test_fields_set_correctly(self) -> None:
        """All fields round-trip correctly."""
        now = _utcnow()
        session = Session(
            id="test-id",
            sdk_session_id="sdk-456",
            transcript_path="/path/to/transcript.jsonl",
            started_at=now,
            ended_at=now,
        )

        assert session.id == "test-id"
        assert session.sdk_session_id == "sdk-456"
        assert session.transcript_path == "/path/to/transcript.jsonl"
        assert session.started_at == now
        assert session.ended_at == now
