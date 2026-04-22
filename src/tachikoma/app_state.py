"""Key-value application state persistence (ADR-013).

Provides a general-purpose app_state table for small pieces of internal
state that survive restarts. Keys are namespaced by convention using
dot-separated prefixes (e.g., updates.last_notified_version).
"""

from datetime import datetime

from sqlalchemy import DateTime, Text, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base


class AppStateModel(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )


class AppStateRepository:
    """Async repository for the app_state key-value table."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get(self, key: str) -> str | None:
        async with self._session_factory() as db:
            result = await db.execute(select(AppStateModel).where(AppStateModel.key == key))
            record = result.scalar_one_or_none()

        return record.value if record is not None else None

    async def set(self, key: str, value: str) -> None:
        async with self._session_factory() as db:
            stmt = sqlite_insert(AppStateModel).values(key=key, value=value)
            stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"value": value})
            await db.execute(stmt)
            await db.commit()
