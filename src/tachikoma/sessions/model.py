"""Session domain model and SQLAlchemy ORM model.

Keeps the ORM model (SessionRecord) internal to the persistence layer.
Callers work exclusively with the frozen Session dataclass.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import DateTime, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
    ended_at: datetime | None = None

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


# ---------------------------------------------------------------------------
# SQLAlchemy ORM — internal to the persistence layer
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    """SQLAlchemy ORM model for the sessions table.

    Internal to the persistence layer; callers never see this type.
    Use to_domain() to convert to the Session dataclass.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(primary_key=True)
    sdk_session_id: Mapped[str | None] = mapped_column(default=None)
    transcript_path: Mapped[str | None] = mapped_column(default=None)
    # DateTime(timezone=True) ensures SQLite stores ISO strings with UTC offset
    # so datetimes round-trip with their tzinfo intact.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_sessions_started_at", "started_at"),
    )

    def to_domain(self) -> Session:
        """Convert ORM record to domain dataclass.

        SQLite stores datetimes as naive ISO strings. Re-attach UTC tzinfo
        on the way out so callers always receive timezone-aware datetimes.
        """
        return Session(
            id=self.id,
            sdk_session_id=self.sdk_session_id,
            transcript_path=self.transcript_path,
            started_at=_ensure_utc(self.started_at),  # type: ignore[arg-type]
            ended_at=_ensure_utc(self.ended_at),
        )


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Re-attach UTC tzinfo to a naive datetime read from SQLite.

    SQLite stores datetimes as text without timezone info. aiosqlite/SQLAlchemy
    reads them back as naive datetimes. This helper restores the UTC context.
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)

    return dt
