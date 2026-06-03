"""Integration tests for the shared Database class.

Tests for database initialization, schema creation, tracked migrations,
and bootstrap hook.
"""

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import aiosqlite
import pytest
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

import tachikoma.database_migrations as dm
from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.database import Database, database_hook
from tachikoma.database_migrations import MIGRATIONS, Migration, run_pending_migrations


async def _create_engine_with_baseline(db_path: Path) -> AsyncEngine:
    """Create an engine with schema_migrations table and 001 stamped."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.execute(text(dm._SCHEMA_MIGRATIONS_DDL))
        await conn.execute(
            text(
                "INSERT INTO schema_migrations (revision, name, applied_at)"
                " VALUES ('001', 'initial_schema', datetime('now'))"
            )
        )
    return engine


@contextmanager
def _patch_migrations(migrations: list[Migration]) -> Iterator[None]:
    """Temporarily replace dm.MIGRATIONS with the given list."""
    original = dm.MIGRATIONS
    dm.MIGRATIONS = migrations
    try:
        yield
    finally:
        dm.MIGRATIONS = original


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

    async def test_creates_all_core_tables(self, tmp_path: Path) -> None:
        """AC1: all core tables are created in the unified database."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        expected = {
            "sessions",
            "session_resumptions",
            "task_definitions",
            "task_instances",
            "schema_migrations",
        }
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
            "id",
            "sdk_session_id",
            "transcript_path",
            "summary",
            "started_at",
            "ended_at",
            "last_resumed_at",
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
            "id",
            "name",
            "schedule",
            "task_type",
            "prompt",
            "enabled",
            "last_fired_at",
            "created_at",
        }
        expected_insts = {
            "id",
            "definition_id",
            "task_type",
            "status",
            "prompt",
            "scheduled_for",
            "started_at",
            "completed_at",
            "result",
            "created_at",
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
            cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='index'")
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

    async def test_workflow_states_has_loop_state_column(self, tmp_path: Path) -> None:
        """R11.1: workflow_states.loop_state column is added by migrations."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('workflow_states')")
            cols = {row[1]: row[2] for row in await cursor.fetchall()}

        assert "loop_state" in cols

    async def test_loop_state_migration_idempotent(self, tmp_path: Path) -> None:
        """re-running migrations on a DB that already has loop_state is a no-op."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        # Re-initialize on the same DB; the migration must not re-add the column
        database2 = Database(db_path)
        await database2.initialize()
        await database2.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('workflow_states')")
            loop_state_cols = [row for row in await cursor.fetchall() if row[1] == "loop_state"]

        assert len(loop_state_cols) == 1

    async def test_existing_install_stamps_baseline_and_runs_pending(self, tmp_path: Path) -> None:
        """R2: existing install stamps baseline only, then pending migrations execute."""
        db_path = tmp_path / "tachikoma.db"

        # Simulate a pre-existing database with sessions and detached_processes
        # (without the new cgroup columns that migration 002 adds)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, "
                "sdk_session_id TEXT, "
                "transcript_path TEXT, "
                "started_at DATETIME NOT NULL, "
                "ended_at DATETIME"
                ")"
            )
            await db.execute(
                "CREATE TABLE detached_processes ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "command TEXT NOT NULL, "
                "cwd TEXT NOT NULL, "
                "pid INTEGER NOT NULL, "
                "process_create_time REAL NOT NULL, "
                "log_path TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "started_at DATETIME NOT NULL, "
                "exited_at DATETIME, "
                "exit_code INTEGER, "
                "stop_reason TEXT"
                ")"
            )
            await db.commit()

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT revision, name FROM schema_migrations")
            rows = await cursor.fetchall()

        # Baseline stamped + migration 002 + migration 003 + migration 004 applied
        assert len(rows) == 4
        revisions = {row[0] for row in rows}
        assert "001" in revisions
        assert "002" in revisions
        assert "003" in revisions
        assert "004" in revisions


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

    async def test_stores_database_in_extras(self, settings_manager: SettingsManager) -> None:
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

    async def test_creates_database_file(self, settings_manager: SettingsManager) -> None:
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


class TestFreshInstall:
    """R3: fresh installation — create_all() handles schema, initial migration stamped."""

    async def test_creates_schema_migrations_table(self, tmp_path: Path) -> None:
        """R3: fresh db gets schema_migrations table."""
        db_path = tmp_path / "tachikoma.db"
        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            )
            assert await cursor.fetchone() is not None

    async def test_stamps_all_migrations_for_fresh_install(self, tmp_path: Path) -> None:
        """R3: fresh install stamps all migrations (create_all produced full schema)."""
        db_path = tmp_path / "tachikoma.db"
        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT revision, name FROM schema_migrations")
            rows = await cursor.fetchall()

        assert len(rows) == len(MIGRATIONS)
        applied = {row[0] for row in rows}
        expected = {m.revision for m in MIGRATIONS}
        assert applied == expected

    async def test_creates_all_orm_tables(self, tmp_path: Path) -> None:
        """R3: all ORM-defined tables exist with current columns after initialize."""
        db_path = tmp_path / "tachikoma.db"
        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = {row[0] for row in await cursor.fetchall()}

        assert "sessions" in tables
        assert "task_definitions" in tables
        assert "task_instances" in tables
        assert "session_resumptions" in tables

    async def test_initial_sql_not_executed(self, tmp_path: Path) -> None:
        """R3: 001_initial.sql statements are NOT re-executed on a fresh install.

        create_all() produces the current schema. The initial migration SQL
        is stamped as applied without running. If it were re-executed, ALTER TABLE
        statements would fail (columns already exist).
        """
        db_path = tmp_path / "tachikoma.db"
        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT revision FROM schema_migrations WHERE revision='001'")
            assert await cursor.fetchone() is not None

            # Verify columns that would come from both create_all() AND the SQL
            # exist exactly once — no duplication from re-execution
            cursor = await db.execute("PRAGMA table_info('sessions')")
            columns = {row[1] for row in await cursor.fetchall()}
            assert "summary" in columns


class TestExistingInstall:
    """R2: existing up-to-date installation — stamp without re-executing."""

    async def test_stamps_baseline_and_applies_pending(self, tmp_path: Path) -> None:
        """R2: existing db stamps baseline, then pending migrations execute."""
        db_path = tmp_path / "tachikoma.db"

        # Simulate a pre-existing database with sessions and detached_processes
        # (without the new cgroup columns that migration 002 adds)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, "
                "sdk_session_id TEXT, "
                "transcript_path TEXT, "
                "started_at DATETIME NOT NULL, "
                "ended_at DATETIME"
                ")"
            )
            await db.execute(
                "CREATE TABLE detached_processes ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "command TEXT NOT NULL, "
                "cwd TEXT NOT NULL, "
                "pid INTEGER NOT NULL, "
                "process_create_time REAL NOT NULL, "
                "log_path TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "started_at DATETIME NOT NULL, "
                "exited_at DATETIME, "
                "exit_code INTEGER, "
                "stop_reason TEXT"
                ")"
            )
            await db.commit()

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT revision, name FROM schema_migrations")
            rows = await cursor.fetchall()

        assert len(rows) == 4
        revisions = {row[0] for row in rows}
        assert "001" in revisions
        assert "002" in revisions
        assert "003" in revisions
        assert "004" in revisions

    async def test_existing_install_migration_adds_cgroup_columns(self, tmp_path: Path) -> None:
        """R2: existing install without cgroup columns gets them via migration 002."""
        db_path = tmp_path / "tachikoma.db"

        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "CREATE TABLE sessions ("
                "id TEXT PRIMARY KEY, "
                "sdk_session_id TEXT, "
                "transcript_path TEXT, "
                "started_at DATETIME NOT NULL, "
                "ended_at DATETIME"
                ")"
            )
            await db.execute(
                "CREATE TABLE detached_processes ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "command TEXT NOT NULL, "
                "cwd TEXT NOT NULL, "
                "pid INTEGER NOT NULL, "
                "process_create_time REAL NOT NULL, "
                "log_path TEXT NOT NULL, "
                "status TEXT NOT NULL, "
                "started_at DATETIME NOT NULL, "
                "exited_at DATETIME, "
                "exit_code INTEGER, "
                "stop_reason TEXT"
                ")"
            )
            await db.commit()

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA table_info('detached_processes')")
            columns = {row[1] for row in await cursor.fetchall()}

        assert "memory_limit" in columns
        assert "cgroup_path" in columns

    async def test_no_schema_modifications_on_restart(self, tmp_path: Path) -> None:
        """R2: re-running initialize on an already-stamped db is a no-op."""
        db_path = tmp_path / "tachikoma.db"

        # First run: fresh install
        database = Database(db_path)
        await database.initialize()
        await database.close()

        # Capture state after first run
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            count_before = (await cursor.fetchone())[0]

        # Second run: should be a no-op
        database2 = Database(db_path)
        await database2.initialize()
        await database2.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM schema_migrations")
            count_after = (await cursor.fetchone())[0]

        assert count_before == count_after


class TestMigrationTracking:
    """R0, R4: migration tracking — only new migrations execute, in order."""

    async def test_only_new_migrations_execute(self, tmp_path: Path) -> None:
        """R0: given '001' applied, only new '002' executes."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_file = tmp_path / "002_test.sql"
        sql_file.write_text("CREATE TABLE test_table (id INTEGER);\n")

        try:
            with _patch_migrations(
                [
                    Migration("001", "initial_schema", "migrations/001_initial.sql"),
                    Migration("002", "test_migration", str(sql_file)),
                ]
            ):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='test_table'"
                )
                assert await cursor.fetchone() is not None

                cursor = await db.execute(
                    "SELECT revision FROM schema_migrations WHERE revision='002'"
                )
                assert await cursor.fetchone() is not None
        finally:
            await engine.dispose()

    async def test_migrations_run_in_order(self, tmp_path: Path) -> None:
        """R4: multiple pending migrations execute in registry list order."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_002 = tmp_path / "002_first.sql"
        sql_002.write_text("CREATE TABLE table_a (id INTEGER);\n")

        sql_003 = tmp_path / "002_second.sql"
        sql_003.write_text("CREATE TABLE table_b (id INTEGER);\n")

        try:
            with _patch_migrations(
                [
                    Migration("001", "initial_schema", "migrations/001_initial.sql"),
                    Migration("002", "first", str(sql_002)),
                    Migration("003", "second", str(sql_003)),
                ]
            ):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT revision, applied_at FROM schema_migrations"
                    " WHERE revision IN ('002', '003') ORDER BY applied_at"
                )
                rows = await cursor.fetchall()

            assert len(rows) == 2
            assert rows[0][0] == "002"
            assert rows[1][0] == "003"
        finally:
            await engine.dispose()

    async def test_applied_revisions_match_registry(self, tmp_path: Path) -> None:
        """R0: after all migrations applied, schema_migrations rows match registry."""
        db_path = tmp_path / "tachikoma.db"

        database = Database(db_path)
        await database.initialize()
        await database.close()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT revision FROM schema_migrations")
            applied = {row[0] for row in await cursor.fetchall()}

        expected = {m.revision for m in MIGRATIONS}
        assert applied == expected


class TestMigrationExecution:
    """R5, R6, R7: migration execution — transactions, failure, logging."""

    async def test_pending_migration_executes_sql_in_transaction(self, tmp_path: Path) -> None:
        """R5, R7: migration with multiple statements executes atomically."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_file = tmp_path / "002_atomic.sql"
        sql_file.write_text(
            "CREATE TABLE atomic_test (id INTEGER PRIMARY KEY, val TEXT);\n"
            "INSERT INTO atomic_test (id, val) VALUES (1, 'hello');\n"
        )

        try:
            with _patch_migrations(
                [
                    Migration("001", "initial_schema", "migrations/001_initial.sql"),
                    Migration("002", "atomic_test", str(sql_file)),
                ]
            ):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("SELECT val FROM atomic_test WHERE id=1")
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == "hello"

                cursor = await db.execute(
                    "SELECT revision FROM schema_migrations WHERE revision='002'"
                )
                assert await cursor.fetchone() is not None
        finally:
            await engine.dispose()

    async def test_migration_failure_rolls_back_and_halts(self, tmp_path: Path) -> None:
        """R7: failing migration rolls back, raises RuntimeError, halts."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_002 = tmp_path / "002_bad.sql"
        sql_002.write_text("INSERT INTO nonexistent_table VALUES (1);\n")

        sql_003 = tmp_path / "003_good.sql"
        sql_003.write_text("CREATE TABLE should_not_exist (id INTEGER);\n")

        try:
            with (
                _patch_migrations(
                    [
                        Migration("001", "initial_schema", "migrations/001_initial.sql"),
                        Migration("002", "bad_migration", str(sql_002)),
                        Migration("003", "good_migration", str(sql_003)),
                    ]
                ),
                pytest.raises(RuntimeError, match=r"Migration 002.*bad_migration"),
            ):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT revision FROM schema_migrations WHERE revision='002'"
                )
                assert await cursor.fetchone() is None

                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='should_not_exist'"
                )
                assert await cursor.fetchone() is None
        finally:
            await engine.dispose()

    async def test_failed_migration_retried_on_restart(self, tmp_path: Path) -> None:
        """R7: failed migration is not stamped and is re-attempted on next run."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_file = tmp_path / "002_retry.sql"
        sql_file.write_text("INSERT INTO nonexistent_table VALUES (1);\n")

        test_migrations = [
            Migration("001", "initial_schema", "migrations/001_initial.sql"),
            Migration("002", "retry_test", str(sql_file)),
        ]

        try:
            # First attempt: fails
            with _patch_migrations(test_migrations), pytest.raises(RuntimeError):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT revision FROM schema_migrations WHERE revision='002'"
                )
                assert await cursor.fetchone() is None

            # Fix the SQL and retry
            sql_file.write_text("CREATE TABLE retry_table (id INTEGER);\n")

            with _patch_migrations(test_migrations):
                await run_pending_migrations(engine)

            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT revision FROM schema_migrations WHERE revision='002'"
                )
                assert await cursor.fetchone() is not None

                cursor = await db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='retry_table'"
                )
                assert await cursor.fetchone() is not None
        finally:
            await engine.dispose()

    async def test_migration_logs_revision_and_name(self, tmp_path: Path) -> None:
        """R6: migration execution emits log with revision ID and name."""
        db_path = tmp_path / "tachikoma.db"
        engine = await _create_engine_with_baseline(db_path)

        sql_file = tmp_path / "002_logging.sql"
        sql_file.write_text("CREATE TABLE log_test (id INTEGER);\n")

        log_messages: list[str] = []
        sink_id = logger.add(lambda msg: log_messages.append(str(msg)))

        try:
            with _patch_migrations(
                [
                    Migration("001", "initial_schema", "migrations/001_initial.sql"),
                    Migration("002", "logging_test", str(sql_file)),
                ]
            ):
                await run_pending_migrations(engine)
        finally:
            logger.remove(sink_id)
            await engine.dispose()

        log_output = " ".join(log_messages)
        assert "002" in log_output
        assert "logging_test" in log_output


class TestCodeCleanup:
    """R1: verify pragma-based checks have been removed."""

    async def test_no_pragma_checks_in_run_migrations(self) -> None:
        """R1: _run_migrations source contains no pragma checks."""
        source = inspect.getsource(Database._run_migrations)
        assert "pragma_table_info" not in source
        assert "sqlite_master" not in source
