"""Plugin state persistence for update tracking.

Stores per-plugin version hashes and update status in the database.
Follows ADR-007 persistence pattern: frozen dataclasses for domain types,
ORM models internal to persistence, repositories receive session_factory via DI.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base
from tachikoma.db_utils import ensure_utc

# ---------------------------------------------------------------------------
# Domain types — public API
# ---------------------------------------------------------------------------

UpdateStatus = Literal["unknown", "up-to-date", "update-available", "stale-fallback"]


@dataclass(frozen=True)
class PluginState:
    """Domain representation of a plugin's update state."""

    alias: str
    installed_version: str | None
    update_status: UpdateStatus
    available_version: str | None
    last_checked_at: datetime | None
    diagnostic: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# SQLAlchemy ORM — internal to the persistence layer
# ---------------------------------------------------------------------------


class PluginStateModel(Base):
    """SQLAlchemy ORM model for the plugin_state table."""

    __tablename__ = "plugin_state"

    alias: Mapped[str] = mapped_column(String, primary_key=True)
    installed_version: Mapped[str | None] = mapped_column(String, nullable=True)
    update_status: Mapped[str] = mapped_column(String, default="unknown")
    available_version: Mapped[str | None] = mapped_column(String, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnostic: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def to_domain(self) -> PluginState:
        """Convert ORM record to domain dataclass."""
        return PluginState(
            alias=self.alias,
            installed_version=self.installed_version,
            update_status=self.update_status,  # type: ignore[arg-type]
            available_version=self.available_version,
            last_checked_at=ensure_utc(self.last_checked_at),
            diagnostic=self.diagnostic,
            created_at=ensure_utc(self.created_at),
        )


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class PluginStateRepository:
    """Async repository for the plugin_state table."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get(self, alias: str) -> PluginState | None:
        """Get plugin state by alias. Returns None if not found."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(PluginStateModel).where(PluginStateModel.alias == alias)
            )
            record = result.scalar_one_or_none()

        return record.to_domain() if record is not None else None

    async def upsert(self, state: PluginState) -> PluginState:
        """Insert or update plugin state. Returns the persisted state."""
        async with self._session_factory() as db:
            values = {
                "alias": state.alias,
                "installed_version": state.installed_version,
                "update_status": state.update_status,
                "available_version": state.available_version,
                "last_checked_at": state.last_checked_at,
                "diagnostic": state.diagnostic,
            }
            stmt = sqlite_insert(PluginStateModel).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["alias"],
                set_={
                    "installed_version": state.installed_version,
                    "update_status": state.update_status,
                    "available_version": state.available_version,
                    "last_checked_at": state.last_checked_at,
                    "diagnostic": state.diagnostic,
                },
            )
            await db.execute(stmt)
            await db.commit()

        return await self.get(state.alias)  # type: ignore[return-value]

    async def remove(self, alias: str) -> None:
        """Remove plugin state by alias."""
        async with self._session_factory() as db:
            result = await db.execute(
                select(PluginStateModel).where(PluginStateModel.alias == alias)
            )
            record = result.scalar_one_or_none()
            if record is not None:
                await db.delete(record)
                await db.commit()
