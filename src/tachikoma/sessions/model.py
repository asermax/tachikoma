"""Session domain model and SQLAlchemy ORM model.

Keeps the ORM model (SessionRecord) internal to the persistence layer.
Callers work exclusively with the frozen Session dataclass.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base
from tachikoma.db_utils import ensure_utc

SessionStatus = Literal["open", "closed", "interrupted"]


@dataclass(frozen=True)
class Session:
    """Domain representation of a conversation session.

    Returned to all callers; has no SQLAlchemy dependency.
    """

    id: str
    started_at: datetime
    sdk_session_id: str | None = None
    transcript_path: str | None = None
    summary: str | None = None
    ended_at: datetime | None = None
    last_resumed_at: datetime | None = None
    processed_at: datetime | None = None

    @property
    def status(self) -> SessionStatus:
        """Derived status — never stored in the database.

        - open:        ended_at is None
        - closed:      ended_at is set AND sdk_session_id is set
        - interrupted: ended_at is set AND sdk_session_id is None
        """
        if self.ended_at is None:
            return "open"

        if self.sdk_session_id is not None:
            return "closed"

        return "interrupted"


@dataclass(frozen=True)
class SessionResumption:
    """Domain representation of a session resumption event.

    Tracks when a session was reopened to continue a previous conversation.
    """

    session_id: str
    resumed_at: datetime
    previous_ended_at: datetime


@dataclass(frozen=True)
class SessionContextEntry:
    """Domain representation of a context entry injected into a session.

    Each entry captures a piece of context that was provided to the agent
    during a session. Entries are persisted to enable context reconstruction
    across per-message SDK client recreations.

    The autoincrement id determines assembly order (insertion order).
    """

    id: int
    session_id: str
    owner: str
    content: str


# ---------------------------------------------------------------------------
# SQLAlchemy ORM — internal to the persistence layer
# ---------------------------------------------------------------------------


class SessionRecord(Base):
    """SQLAlchemy ORM model for the sessions table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the Session dataclass.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(primary_key=True)
    sdk_session_id: Mapped[str | None] = mapped_column(default=None)
    transcript_path: Mapped[str | None] = mapped_column(default=None)
    summary: Mapped[str | None] = mapped_column(default=None)
    # DateTime(timezone=True) ensures SQLite stores ISO strings with UTC offset
    # so datetimes round-trip with their tzinfo intact.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (Index("ix_sessions_started_at", "started_at"),)

    def to_domain(self) -> Session:
        """Convert ORM record to domain dataclass.

        SQLite stores datetimes as naive ISO strings. Re-attach UTC tzinfo
        on the way out so callers always receive timezone-aware datetimes.
        """
        return Session(
            id=self.id,
            sdk_session_id=self.sdk_session_id,
            transcript_path=self.transcript_path,
            summary=self.summary,
            started_at=ensure_utc(self.started_at),  # type: ignore[arg-type]
            ended_at=ensure_utc(self.ended_at),
            last_resumed_at=ensure_utc(self.last_resumed_at),
            processed_at=ensure_utc(self.processed_at),
        )


class SessionResumptionRecord(Base):
    """SQLAlchemy ORM model for the session_resumptions table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the SessionResumption dataclass.
    """

    __tablename__ = "session_resumptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    resumed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    previous_ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_session_resumptions_session_id", "session_id"),)

    def to_domain(self) -> SessionResumption:
        """Convert ORM record to domain dataclass."""
        return SessionResumption(
            session_id=self.session_id,
            resumed_at=ensure_utc(self.resumed_at),  # type: ignore[arg-type]
            previous_ended_at=ensure_utc(self.previous_ended_at),  # type: ignore[arg-type]
        )


class SessionContextEntryRecord(Base):
    """SQLAlchemy ORM model for the session_context_entries table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the SessionContextEntry dataclass.

    The autoincrement id determines assembly order (insertion order).
    """

    __tablename__ = "session_context_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    owner: Mapped[str] = mapped_column()
    content: Mapped[str] = mapped_column()

    __table_args__ = (Index("ix_session_context_entries_session_id", "session_id"),)

    def to_domain(self) -> SessionContextEntry:
        """Convert ORM record to domain dataclass."""
        return SessionContextEntry(
            id=self.id,
            session_id=self.session_id,
            owner=self.owner,
            content=self.content,
        )
