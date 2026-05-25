"""Migration definitions, registry, and runner for tracked schema migrations.

Defines the Migration dataclass, an ordered registry of all known migrations,
and an async runner that queries applied revisions, diffs against the registry,
and executes pending migrations with per-migration transactions.
"""

import importlib.resources
from dataclasses import dataclass

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_log = logger.bind(component="database")

_SCHEMA_MIGRATIONS_DDL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    revision VARCHAR NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_STAMP_SQL = text(
    "INSERT INTO schema_migrations (revision, name, applied_at)"
    " VALUES (:rev, :name, datetime('now'))"
)


@dataclass(frozen=True)
class Migration:
    """An immutable migration definition.

    Attributes:
        revision: Unique revision identifier (e.g., "001", "002").
        name: Human-readable migration name (e.g., "initial_schema").
        sql_path: Relative path to the SQL file under src/tachikoma/.
    """

    revision: str
    name: str
    sql_path: str


MIGRATIONS: list[Migration] = [
    Migration("001", "initial_schema", "migrations/001_initial.sql"),
    Migration(
        "002",
        "add_cgroup_fields_to_detached_processes",
        "migrations/002_add_cgroup_fields_to_detached_processes.sql",
    ),
]


async def run_pending_migrations(engine: AsyncEngine, *, is_fresh_db: bool = False) -> None:
    """Run any pending tracked migrations against the database.

    Creates the ``schema_migrations`` table if absent, stamps migrations
    without executing SQL for initial installs (all for fresh, baseline only
    for existing), then diffs applied revisions against the registry and
    executes pending migrations in order — each in its own transaction.

    Args:
        engine: AsyncEngine for the database.
        is_fresh_db: True if the database had no tables before ``create_all()``
            ran. Must be determined *before* ``create_all()`` since it creates
            all ORM tables and makes the database appear non-empty.

    Raises:
        RuntimeError: If a migration fails, identifying revision and name.
    """
    async with engine.begin() as conn:
        await conn.execute(text(_SCHEMA_MIGRATIONS_DDL))

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT revision FROM schema_migrations"))
        applied_set = {row[0] for row in result.fetchall()}

    if not applied_set:
        migrations_to_stamp = MIGRATIONS if is_fresh_db else [MIGRATIONS[0]]

        async with engine.begin() as conn:
            for m in migrations_to_stamp:
                await conn.execute(_STAMP_SQL, {"rev": m.revision, "name": m.name})

        _log.info(
            "Schema initialized ({type} install). {count} migration(s) stamped.",
            type="fresh" if is_fresh_db else "existing",
            count=len(migrations_to_stamp),
        )

        applied_set = {m.revision for m in migrations_to_stamp}

    pending = [m for m in MIGRATIONS if m.revision not in applied_set]

    if not pending:
        _log.debug("Schema is up to date")
        return

    for m in pending:
        try:
            resource = importlib.resources.files("tachikoma").joinpath(m.sql_path)
            sql_content = resource.read_text("utf-8")
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Migration {m.revision} ({m.name}): SQL file not found: {m.sql_path}"
            ) from e

        statements = [s.strip() for s in sql_content.split(";\n") if s.strip()]

        try:
            async with engine.begin() as conn:
                for statement in statements:
                    await conn.execute(text(statement))
                await conn.execute(_STAMP_SQL, {"rev": m.revision, "name": m.name})
        except Exception as e:
            raise RuntimeError(f"Migration {m.revision} ({m.name}) failed: {e}") from e

        _log.info(
            "Applied migration {revision}: {name}",
            revision=m.revision,
            name=m.name,
        )
