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
]


async def run_pending_migrations(engine: AsyncEngine) -> None:
    """Run any pending tracked migrations against the database.

    Creates the ``schema_migrations`` table if absent, stamps the initial
    migration (``001``) for both fresh and existing installs without executing
    its SQL, then diffs applied revisions against the registry and executes
    pending migrations in order — each in its own transaction.

    Raises:
        RuntimeError: If a migration fails, identifying revision and name.
    """
    async with engine.begin() as conn:
        await conn.execute(text(_SCHEMA_MIGRATIONS_DDL))

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT revision FROM schema_migrations"))
        applied_set = {row[0] for row in result.fetchall()}

    if not applied_set:
        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'")
            )
            has_sessions = result.fetchone() is not None

        install_type = "existing" if has_sessions else "fresh"

        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO schema_migrations (revision, name, applied_at)"
                    " VALUES (:rev, :name, datetime('now'))"
                ),
                {"rev": "001", "name": "initial_schema"},
            )

        _log.info(
            "Schema initialized ({type} install). Migration 001 stamped.",
            type=install_type,
        )

        applied_set = {"001"}

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
                await conn.execute(
                    text(
                        "INSERT INTO schema_migrations (revision, name, applied_at)"
                        " VALUES (:rev, :name, datetime('now'))"
                    ),
                    {"rev": m.revision, "name": m.name},
                )
        except Exception as e:
            raise RuntimeError(f"Migration {m.revision} ({m.name}) failed: {e}") from e

        _log.info(
            "Applied migration {revision}: {name}",
            revision=m.revision,
            name=m.name,
        )
