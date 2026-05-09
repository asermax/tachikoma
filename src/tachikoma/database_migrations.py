"""Migration definitions and registry for tracked schema migrations.

Defines the Migration dataclass and an ordered registry of all known migrations.
The registry is the single source of truth for migration metadata — position in
the list determines execution order.
"""

from dataclasses import dataclass


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
