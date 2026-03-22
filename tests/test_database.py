"""Integration tests for the shared Database class.

Tests for database initialization, schema creation, and bootstrap hook.
"""

from pathlib import Path

import aiosqlite
import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.database import Database, database_hook


class TestDatabaseInitialization:
    """Tests for Database.initialize() and schema creation."""

    async def test_creates_database_file(self, tmp_path: Path) -> None:
        """AC1: database file does not exist -> created on initialize()."""
        db_path = tmp_path / "tachikoma.db"
        assert not db_path.exists()

        database = Database(db_path)
        await database.initialize()
        await database.close()

        assert db_path.exists()

    async def test_creates_all_four_tables(self, tmp_path: Path) -> None:
        """AC1: all 4 tables are created in the unified database."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        expected = {"sessions", "session_resumptions", "task_definitions", "task_instances"}
        assert expected.issubset(tables)

    async def test_creates_sessions_columns(self, tmp_path: Path) -> None:
        """AC1: sessions table has all expected columns."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('sessions')")
            columns = {row[1] for row in await cursor.fetchall()}

        expected = {
            "id", "sdk_session_id", "transcript_path", "summary",
            "started_at", "ended_at", "last_resumed_at",
        }
        assert expected.issubset(columns)

    async def test_creates_task_tables_columns(self, tmp_path: Path) -> None:
        """AC1: task tables have all expected columns."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('task_definitions')")
            def_columns = {row[1] for row in await cursor.fetchall()}

            cursor = await db.execute("PRAGMA table_info('task_instances')")
            inst_columns = {row[1] for row in await cursor.fetchall()}

        expected_defs = {
            "id", "name", "schedule", "task_type", "prompt",
            "notify", "enabled", "last_fired_at", "created_at",
        }
        expected_insts = {
            "id", "definition_id", "task_type", "status", "prompt",
            "scheduled_for", "started_at", "completed_at", "result", "created_at",
        }

        assert expected_defs.issubset(def_columns)
        assert expected_insts.issubset(inst_columns)

    async def test_creates_indexes(self, tmp_path: Path) -> None:
        """AC1: expected indexes are created."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
            indexes = {row[0] for row in await cursor.fetchall()}

        assert "ix_sessions_started_at" in indexes
        assert "ix_session_resumptions_session_id" in indexes
        assert "ix_task_instances_status" in indexes
        assert "ix_task_instances_task_type" in indexes

    async def test_initialize_is_idempotent(self, tmp_path: Path) -> None:
        """Schema creation twice raises no errors."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        # Second initialization on same DB should succeed
        database2 = Database(db_path)
        await database2.initialize()
        await database2.close()


class TestDatabaseClose:
    """Tests for engine disposal."""

    async def test_close_disposes_engine(self, tmp_path: Path) -> None:
        """AC3: close() disposes the engine."""
        database = Database(tmp_path / "tachikoma.db")
        await database.initialize()

        await database.close()

        # After close, session_factory should raise
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = database.session_factory


class TestDatabaseHook:
    """Tests for the database_hook bootstrap hook."""

    async def test_stores_database_in_extras(
        self, settings_manager: SettingsManager
    ) -> None:
        """AC: hook stores database in ctx.extras['database']."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True, exist_ok=True)
        ws.data_path.mkdir(exist_ok=True)

        ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
        await database_hook(ctx)

        assert "database" in ctx.extras
        assert isinstance(ctx.extras["database"], Database)

        # Cleanup
        await ctx.extras["database"].close()

    async def test_creates_database_file(
        self, settings_manager: SettingsManager
    ) -> None:
        """AC: hook creates the tachikoma.db file in the data directory."""
        ws = settings_manager.settings.workspace
        ws.path.mkdir(parents=True, exist_ok=True)
        ws.data_path.mkdir(exist_ok=True)

        db_path = ws.data_path / "tachikoma.db"
        assert not db_path.exists()

        ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
        await database_hook(ctx)

        assert db_path.exists()

        # Cleanup
        await ctx.extras["database"].close()
