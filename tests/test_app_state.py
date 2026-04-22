"""Tests for AppStateModel and AppStateRepository."""

from pathlib import Path

import aiosqlite

from tachikoma.app_state import AppStateRepository
from tachikoma.database import Database


class TestAppStateRepository:
    async def test_get_returns_none_for_missing_key(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()

        repo = AppStateRepository(db.session_factory)
        result = await repo.get("nonexistent")

        assert result is None

        await db.close()

    async def test_set_then_get_returns_value(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()

        repo = AppStateRepository(db.session_factory)
        await repo.set("test.key", "hello")
        result = await repo.get("test.key")

        assert result == "hello"

        await db.close()

    async def test_set_overwrites_existing_value(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()

        repo = AppStateRepository(db.session_factory)
        await repo.set("test.key", "first")
        await repo.set("test.key", "second")
        result = await repo.get("test.key")

        assert result == "second"

        await db.close()

    async def test_updated_at_set_on_creation(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "tachikoma.db")
        await db.initialize()

        repo = AppStateRepository(db.session_factory)
        await repo.set("test.key", "value")

        async with aiosqlite.connect(tmp_path / "tachikoma.db") as conn:
            cursor = await conn.execute("SELECT updated_at FROM app_state WHERE key = 'test.key'")
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] is not None

        await db.close()
