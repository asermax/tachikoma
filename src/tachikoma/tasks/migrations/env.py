"""Alembic migration environment for async SQLAlchemy (tasks database).

This module provides the migration runtime for Tachikoma's task database.
It configures Alembic programmatically (no alembic.ini file) and supports
async engines via run_sync.

Usage:
    The repository.initialize() method can call run_migrations() via
    conn.run_sync() to apply pending migrations.
"""

from alembic import context
from sqlalchemy import Connection, pool

# Import TaskBase for metadata - this is needed for autogenerate support
try:
    from tachikoma.tasks.model import TaskBase

    target_metadata = TaskBase.metadata
except ImportError:
    # Fallback for when running outside of package context
    target_metadata = None


def run_migrations(connection: Connection) -> None:
    """Run migrations in a transaction using the provided connection.

    This function is called via conn.run_sync() from the async repository.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_from_url(url: str) -> None:
    """Run migrations using a database URL (for offline/standalone use).

    This is primarily for CLI usage. The async repository uses run_migrations()
    via run_sync instead.
    """
    from sqlalchemy import create_engine

    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        run_migrations(connection)

    connectable.dispose()
