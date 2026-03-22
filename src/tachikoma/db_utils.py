"""Shared database utilities for the persistence layer."""

from datetime import UTC, datetime


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Re-attach UTC tzinfo to a naive datetime read from SQLite.

    SQLite stores datetimes as text without timezone info. aiosqlite/SQLAlchemy
    reads them back as naive datetimes. This helper restores the UTC context.
    """
    if dt is None:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)

    return dt
