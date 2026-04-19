"""Tests for DB dump and restore."""

import json
import sqlite3
from pathlib import Path

from tachikoma.git.db_sync import dump_database, restore_database


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, blob BLOB)")
    conn.execute("CREATE TABLE tags (label TEXT)")
    conn.executemany(
        "INSERT INTO items (name, blob) VALUES (?, ?)",
        [("first", b"\x00\x01"), ("second", None)],
    )
    conn.executemany("INSERT INTO tags VALUES (?)", [("a",), ("b",)])
    conn.commit()
    conn.close()


class TestDumpDatabase:
    async def test_skips_when_db_does_not_exist(self, tmp_path: Path) -> None:
        """AC: Returns early with no error when DB file doesn't exist."""
        db_path = tmp_path / "nonexistent.db"
        dump_dir = tmp_path / "dump"

        await dump_database(db_path, dump_dir)

        assert not dump_dir.exists()

    async def test_clears_existing_dump_dir(self, tmp_path: Path) -> None:
        """AC: Clears stale dump files before writing new ones."""
        db_path = tmp_path / "test.db"
        _make_db(db_path)
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        stale = dump_dir / "old_table.ndjson"
        stale.write_text("stale data")

        await dump_database(db_path, dump_dir)

        assert not stale.exists()

    async def test_writes_metadata_and_rows(self, tmp_path: Path) -> None:
        """AC: Writes .metadata.json + .ndjson for each table, rows as JSON arrays."""
        db_path = tmp_path / "test.db"
        _make_db(db_path)
        dump_dir = tmp_path / "dump"

        await dump_database(db_path, dump_dir)

        meta = json.loads((dump_dir / "items.metadata.json").read_text())
        assert meta["name"] == "items"
        assert meta["columns"] == ["id", "name", "blob"]
        assert "CREATE TABLE items" in meta["schema"]

        rows = [
            json.loads(line)
            for line in (dump_dir / "items.ndjson").read_text().splitlines()
            if line.strip()
        ]
        assert len(rows) == 2
        assert rows[0][1] == "first"
        assert rows[1][1] == "second"

    async def test_excludes_sqlite_sequence(self, tmp_path: Path) -> None:
        """AC: sqlite_sequence is excluded from the dump."""
        db_path = tmp_path / "test.db"
        _make_db(db_path)
        dump_dir = tmp_path / "dump"

        await dump_database(db_path, dump_dir)

        assert not (dump_dir / "sqlite_sequence.metadata.json").exists()
        assert not (dump_dir / "sqlite_sequence.ndjson").exists()


class TestRestoreDatabase:
    async def test_round_trip_preserves_data(self, tmp_path: Path) -> None:
        """AC: dump + restore reproduces the original data."""
        src = tmp_path / "src.db"
        _make_db(src)
        dump_dir = tmp_path / "dump"
        await dump_database(src, dump_dir)

        dst = tmp_path / "dst.db"
        await restore_database(dst, dump_dir)

        conn = sqlite3.connect(dst)
        try:
            names = [n for (n,) in conn.execute("SELECT name FROM items ORDER BY id")]
            tags = [t for (t,) in conn.execute("SELECT label FROM tags ORDER BY label")]
        finally:
            conn.close()

        assert names == ["first", "second"]
        assert tags == ["a", "b"]

    async def test_replaces_existing_db(self, tmp_path: Path) -> None:
        """AC: Existing DB is deleted before restore."""
        db_path = tmp_path / "test.db"
        db_path.write_text("old garbage")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        # Empty dump dir -> creates empty DB
        await restore_database(db_path, dump_dir)

        assert db_path.exists()
        # Valid (empty) sqlite file, not the garbage text
        conn = sqlite3.connect(db_path)
        try:
            tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        finally:
            conn.close()
        assert tables == []

    async def test_works_when_no_existing_db(self, tmp_path: Path) -> None:
        """AC: Works fine when DB doesn't exist yet."""
        db_path = tmp_path / "test.db"
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()

        await restore_database(db_path, dump_dir)

        assert db_path.exists()
