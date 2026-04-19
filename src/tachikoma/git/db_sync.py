"""Database dump and restore for diffable git tracking.

Dumps the workspace SQLite DB to diffable text files (.ndjson + .metadata.json
per table) for git tracking, and restores the DB from those dumps on sync.

File format matches `sqlite-diffable` (simonw/sqlite-diffable) for compatibility
with previously committed dumps:
- `<table>.metadata.json`: `{"name", "columns", "schema"}`
- `<table>.ndjson`: one JSON array per row, values in column order
"""

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

from loguru import logger

_log = logger.bind(component="git.db_sync")


# sqlite_sequence is SQLite's internal AUTOINCREMENT bookkeeping; it has a
# reserved name and SQLite rejects CREATE/INSERT against it. It rebuilds
# automatically from MAX(rowid) on the next insert into an AUTOINCREMENT table.
_EXCLUDED_TABLES = frozenset({"sqlite_sequence"})


def _list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()

    return [name for (name,) in rows if name not in _EXCLUDED_TABLES]


def _table_schema(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()

    if row is None or row[0] is None:
        raise RuntimeError(f"no schema found for table {table!r}")

    return row[0]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    # PRAGMA table_info returns (cid, name, type, notnull, dflt_value, pk)
    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()

    return [name for (_, name, *_rest) in rows]


def _dump_sync(db_path: Path, dump_dir: Path) -> None:
    shutil.rmtree(dump_dir, ignore_errors=True)
    dump_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        for table in _list_tables(conn):
            columns = _table_columns(conn, table)
            schema = _table_schema(conn, table)

            # Sanitize the filename the same way sqlite-diffable does
            safe_name = table.replace("/", "")

            ndjson_path = dump_dir / f"{safe_name}.ndjson"
            with ndjson_path.open("w") as f:
                quoted_cols = ", ".join(f'"{c}"' for c in columns)
                for row in conn.execute(f'SELECT {quoted_cols} FROM "{table}"'):
                    # `default=repr` matches sqlite-diffable's fallback for
                    # non-JSON-serializable types (e.g., raw bytes)
                    f.write(json.dumps(list(row), default=repr) + "\n")

            meta_path = dump_dir / f"{safe_name}.metadata.json"
            meta_path.write_text(
                json.dumps(
                    {"name": table, "columns": columns, "schema": schema}, indent=4
                )
            )
    finally:
        conn.close()


def _restore_sync(db_path: Path, dump_dir: Path) -> None:
    db_path.unlink(missing_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        for meta_path in sorted(dump_dir.glob("*.metadata.json")):
            info = json.loads(meta_path.read_text())
            table = info["name"]
            columns = info["columns"]
            schema = info["schema"]

            if table in _EXCLUDED_TABLES:
                continue

            # Fresh DB: no need to DROP, just CREATE
            conn.execute(schema)

            ndjson_path = meta_path.parent / meta_path.stem.replace(
                ".metadata", ".ndjson"
            )
            if not ndjson_path.exists():
                continue

            placeholders = ", ".join("?" for _ in columns)
            quoted_cols = ", ".join(f'"{c}"' for c in columns)
            insert_sql = (
                f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
            )

            with ndjson_path.open() as f:
                rows = (json.loads(line) for line in f if line.strip())
                conn.executemany(insert_sql, rows)

        conn.commit()
    finally:
        conn.close()


async def dump_database(db_path: Path, dump_dir: Path) -> None:
    """Dump the workspace DB to diffable text files.

    Clears the dump directory before writing so stale files from dropped
    tables don't accumulate.

    Args:
        db_path: Path to the SQLite database file.
        dump_dir: Directory to write dump files into.
    """
    if not db_path.exists():
        _log.debug("DB file not found, skipping dump: path={path}", path=str(db_path))
        return

    await asyncio.to_thread(_dump_sync, db_path, dump_dir)
    _log.debug("DB dumped to diffable files: dir={dir}", dir=str(dump_dir))


async def restore_database(db_path: Path, dump_dir: Path) -> None:
    """Restore the workspace DB from diffable text files.

    Deletes the existing DB file and rebuilds it from the dump directory.

    Args:
        db_path: Path to the SQLite database file.
        dump_dir: Directory containing dump files.
    """
    await asyncio.to_thread(_restore_sync, db_path, dump_dir)
    _log.info("DB restored from dump files: path={path}", path=str(db_path))
