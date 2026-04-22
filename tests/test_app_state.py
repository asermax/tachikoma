"""Tests for AppStateModel and AppStateRepository."""

from pathlib import Path

import aiosqlite
import pytest

from tachikoma.app_state import AppStateRepository
from tachikoma.database import Database


@pytest.fixture
async def app_state_repo(tmp_path: Path) -> AppStateRepository:
    """Initialized AppStateRepository backed by a temp SQLite file."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield AppStateRepository(database.session_factory)
    await database.close()


class TestAppStateRepository:
    async def test_get_returns_none_for_missing_key(
        self, app_state_repo: AppStateRepository
    ) -> None:
        result = await app_state_repo.get("nonexistent")
        assert result is None

    async def test_set_then_get_returns_value(self, app_state_repo: AppStateRepository) -> None:
        await app_state_repo.set("test.key", "hello")
        result = await app_state_repo.get("test.key")
        assert result == "hello"

    async def test_set_overwrites_existing_value(self, app_state_repo: AppStateRepository) -> None:
        await app_state_repo.set("test.key", "first")
        await app_state_repo.set("test.key", "second")
        result = await app_state_repo.get("test.key")
        assert result == "second"

    async def test_updated_at_set_on_creation(
        self, tmp_path: Path, app_state_repo: AppStateRepository
    ) -> None:
        await app_state_repo.set("test.key", "value")

        async with aiosqlite.connect(tmp_path / "tachikoma.db") as conn:
            cursor = await conn.execute("SELECT updated_at FROM app_state WHERE key = 'test.key'")
            row = await cursor.fetchone()

        assert row is not None
        assert row[0] is not None
