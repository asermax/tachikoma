"""Shared database infrastructure for all persistent subsystems.

Provides a single DeclarativeBase and Database class that centralizes
engine lifecycle. All ORM models inherit from Base; all repositories
receive the shared session_factory.
"""

from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from tachikoma.bootstrap import BootstrapContext
from tachikoma.database_migrations import run_pending_migrations

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
        then uses create_all() for idempotent table creation. Delegates to the
        tracked migration runner for versioned schema changes.
        """
        if self._engine is None:
            return

        import tachikoma.app_state  # noqa: F401, PLC0415
        import tachikoma.detached_processes.model  # noqa: F401, PLC0415
        import tachikoma.plugins.state  # noqa: F401, PLC0415
        import tachikoma.sessions.model  # noqa: F401, PLC0415
        import tachikoma.tasks.model  # noqa: F401, PLC0415
        import tachikoma.workflows.model  # noqa: F401, PLC0415

        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Delegate to tracked migration runner
        await run_pending_migrations(self._engine)

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

    Keys written to ctx.extras:
        "database" -> Database instance
    """
    _log.info("Database hook started")

    data_path = ctx.settings_manager.settings.workspace.data_path
    db_path = data_path / "tachikoma.db"

    database = Database(db_path)
    await database.initialize()

    ctx.extras["database"] = database

    _log.info("Database hook completed")
