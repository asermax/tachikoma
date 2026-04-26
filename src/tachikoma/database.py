"""Shared database infrastructure for all persistent subsystems.

Provides a single DeclarativeBase and Database class that centralizes
engine lifecycle. All ORM models inherit from Base; all repositories
receive the shared session_factory.
"""

from datetime import UTC, datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="database")


class Base(DeclarativeBase):
    """Single shared base class for all ORM models."""

    pass


class Database:
    """Centralized async database engine and session factory.

    Owns the AsyncEngine lifecycle for the unified tachikoma.db file.
    Both SessionRepository and TaskRepository receive the session_factory
    from this class.

    Usage::

        db = Database(data_path / "tachikoma.db")
        await db.initialize()
        try:
            repo = SomeRepository(db.session_factory)
            ...
        finally:
            await db.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    @property
    def session_factory(self) -> async_sessionmaker:
        """Return the shared async session factory.

        Raises RuntimeError if the database has not been initialized.
        """
        if self._session_factory is None:
            raise RuntimeError("Database is not initialized. Call initialize() first.")

        return self._session_factory

    async def initialize(self) -> None:
        """Create the async engine, session factory, and run migrations.

        Imports both model modules to ensure their ORM classes are registered
        on Base.metadata before running create_all().

        Safe to call on separate instances against the same database file.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        url = f"sqlite+aiosqlite:///{self._db_path}"
        self._engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

        await self._run_migrations()

        _log.info("Database initialized: db_path={path}", path=self._db_path)

    async def _run_migrations(self) -> None:
        """Run schema migrations.

        Imports model modules so their ORM classes register on Base.metadata,
        then uses create_all() for idempotent table creation. Pragma-based
        column checks handle upgrades of existing databases.
        """
        if self._engine is None:
            return

        import tachikoma.app_state  # noqa: F401, PLC0415
        import tachikoma.detached_processes.model  # noqa: F401, PLC0415
        import tachikoma.sessions.model  # noqa: F401, PLC0415
        import tachikoma.tasks.model  # noqa: F401, PLC0415
        import tachikoma.workflows.model  # noqa: F401, PLC0415

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

            # Check for and add missing columns on existing databases

            # Check if summary column exists on sessions table (added in DLT-027)
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='summary'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE sessions ADD COLUMN summary TEXT"))
                _log.info("Schema migration: added 'summary' column to sessions table")

            # Check if last_resumed_at column exists on sessions table (added in DLT-028)
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='last_resumed_at'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE sessions ADD COLUMN last_resumed_at DATETIME"))
                _log.info("Schema migration: added 'last_resumed_at' column to sessions table")

            # Check if processed_at column exists on sessions table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='processed_at'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE sessions ADD COLUMN processed_at DATETIME"))
                _log.info("Schema migration: added 'processed_at' column to sessions table")

            # Check if session_resumptions table exists (added in DLT-028)
            result = await conn.execute(
                text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name='session_resumptions'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("""
                        CREATE TABLE session_resumptions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL REFERENCES sessions(id),
                            resumed_at DATETIME NOT NULL,
                            previous_ended_at DATETIME NOT NULL
                        )
                    """)
                )
                await conn.execute(
                    text(
                        "CREATE INDEX ix_session_resumptions_session_id"
                        " ON session_resumptions(session_id)"
                    )
                )
                _log.info("Schema migration: created 'session_resumptions' table")

            # Check if session_context_entries table exists
            result = await conn.execute(
                text(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name='session_context_entries'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("""
                        CREATE TABLE session_context_entries (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            session_id TEXT NOT NULL REFERENCES sessions(id),
                            owner TEXT NOT NULL,
                            content TEXT NOT NULL
                        )
                    """)
                )
                await conn.execute(
                    text(
                        "CREATE INDEX ix_session_context_entries_session_id"
                        " ON session_context_entries(session_id)"
                    )
                )
                _log.info("Schema migration: created 'session_context_entries' table")

            # Check if notify column exists on task_definitions table (removed in DLT-091)
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('task_definitions') WHERE name='notify'")
            )
            if result.fetchone() is not None:
                await conn.execute(text("ALTER TABLE task_definitions DROP COLUMN notify"))
                _log.info("Schema migration: dropped 'notify' column from task_definitions table")

            # Check if error column exists on sessions table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='error'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE sessions ADD COLUMN error BOOLEAN DEFAULT 0"))
                _log.info("Schema migration: added 'error' column to sessions table")

            # Check if last_exchange column exists on sessions table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('sessions') WHERE name='last_exchange'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE sessions ADD COLUMN last_exchange TEXT"))
                _log.info("Schema migration: added 'last_exchange' column to sessions table")

            # Check if metadata column exists on session_context_entries table
            result = await conn.execute(
                text(
                    "SELECT * FROM pragma_table_info('session_context_entries')"
                    " WHERE name='metadata'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE session_context_entries ADD COLUMN metadata TEXT")
                )
                _log.info(
                    "Schema migration: added 'metadata' column to session_context_entries table"
                )

            # Check if sdk_session_id column exists on task_instances table
            result = await conn.execute(
                text(
                    "SELECT * FROM pragma_table_info('task_instances') WHERE name='sdk_session_id'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE task_instances ADD COLUMN sdk_session_id TEXT")
                )
                _log.info("Schema migration: added 'sdk_session_id' column to task_instances table")

            # Check if user_response column exists on task_instances table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('task_instances') WHERE name='user_response'")
            )
            if result.fetchone() is None:
                await conn.execute(text("ALTER TABLE task_instances ADD COLUMN user_response TEXT"))
                _log.info("Schema migration: added 'user_response' column to task_instances table")

            # Check if updated_at column exists on task_instances table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('task_instances') WHERE name='updated_at'")
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE task_instances ADD COLUMN updated_at DATETIME")
                )
                _log.info("Schema migration: added 'updated_at' column to task_instances table")

            # Check if since column exists on task_definitions table
            result = await conn.execute(
                text("SELECT * FROM pragma_table_info('task_definitions') WHERE name='since'")
            )
            if result.fetchone() is None:
                since_default = datetime.now(UTC).isoformat()
                await conn.execute(
                    text(
                        "ALTER TABLE task_definitions ADD COLUMN since DATETIME"
                        f" NOT NULL DEFAULT '{since_default}'"
                    )
                )
                _log.info("Schema migration: added 'since' column to task_definitions table")

            # Check if stop_reason column exists on detached_processes table
            result = await conn.execute(
                text(
                    "SELECT * FROM pragma_table_info('detached_processes') WHERE name='stop_reason'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE detached_processes ADD COLUMN stop_reason TEXT")
                )
                _log.info(
                    "Schema migration: added 'stop_reason' column to detached_processes table"
                )

            # Check if parent_workflow_id column exists on workflow_states table (added in DLT-161)
            result = await conn.execute(
                text(
                    "SELECT * FROM pragma_table_info('workflow_states')"
                    " WHERE name='parent_workflow_id'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE workflow_states ADD COLUMN parent_workflow_id TEXT")
                )
                _log.info(
                    "Schema migration: added 'parent_workflow_id' column to workflow_states table"
                )

            # Check if parent_step_id column exists on workflow_states table (added in DLT-161)
            result = await conn.execute(
                text(
                    "SELECT * FROM pragma_table_info('workflow_states')"
                    " WHERE name='parent_step_id'"
                )
            )
            if result.fetchone() is None:
                await conn.execute(
                    text("ALTER TABLE workflow_states ADD COLUMN parent_step_id TEXT")
                )
                _log.info(
                    "Schema migration: added 'parent_step_id' column to workflow_states table"
                )

        _log.debug("Schema migrations completed: db_path={path}", path=self._db_path)

    async def close(self) -> None:
        """Dispose the async engine and release all connections."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None


async def database_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize the shared database.

    Creates the Database instance, runs migrations, and stores it in
    ctx.extras for retrieval by downstream hooks and __main__.py.

    If the DB file is missing but dump files exist (e.g., fresh clone,
    deleted DB), restores from dump before initializing the engine.

    Keys written to ctx.extras:
        "database" -> Database instance
    """
    _log.info("Database hook started")

    data_path = ctx.settings_manager.settings.workspace.data_path
    db_path = data_path / "tachikoma.db"
    dump_dir = data_path / "db-dump"

    # Restore from dump if DB is missing but dumps exist
    if not db_path.exists() and dump_dir.exists() and any(dump_dir.iterdir()):
        try:
            from tachikoma.git.db_sync import restore_database  # noqa: PLC0415

            _log.info("DB file missing, restoring from dump files")
            await restore_database(db_path, dump_dir)
        except Exception as e:
            _log.warning(
                "DB restore from dump failed, will create fresh DB: err={err}",
                err=str(e),
            )

    database = Database(db_path)
    await database.initialize()

    ctx.extras["database"] = database

    _log.info("Database hook completed")
