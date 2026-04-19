"""Tests for DB dump and restore via sqlite-diffable."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tachikoma.git.db_sync import dump_database, restore_database


class AsyncSubprocessMock:
    """Mock for asyncio.subprocess.Process."""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr


class TestDumpDatabase:
    """Tests for dump_database."""

    async def test_skips_when_db_does_not_exist(self, tmp_path: Path) -> None:
        """AC: Returns early with no error when DB file doesn't exist."""
        db_path = tmp_path / "nonexistent.db"
        dump_dir = tmp_path / "dump"

        await dump_database(db_path, dump_dir)

        assert not dump_dir.exists()

    async def test_clears_existing_dump_dir(self, tmp_path: Path) -> None:
        """AC: Clears stale dump files before writing new ones."""
        db_path = tmp_path / "test.db"
        db_path.write_text("fake db")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()
        stale_file = dump_dir / "old_table.ndjson"
        stale_file.write_text("stale data")

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await dump_database(db_path, dump_dir)

        # Stale file should be gone (directory was cleared and recreated)
        assert not stale_file.exists()

    async def test_creates_dump_dir(self, tmp_path: Path) -> None:
        """AC: Creates dump directory if it doesn't exist."""
        db_path = tmp_path / "test.db"
        db_path.write_text("fake db")
        dump_dir = tmp_path / "dump"

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await dump_database(db_path, dump_dir)

        assert dump_dir.exists()

    async def test_raises_on_subprocess_failure(self, tmp_path: Path) -> None:
        """AC: Raises RuntimeError when sqlite-diffable exits non-zero."""
        db_path = tmp_path / "test.db"
        db_path.write_text("fake db")
        dump_dir = tmp_path / "dump"

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=AsyncSubprocessMock(
                    returncode=1, stderr=b"Error: invalid database"
                ),
            ),
            pytest.raises(RuntimeError, match="sqlite-diffable dump"),
        ):
            await dump_database(db_path, dump_dir)

    async def test_calls_sqlite_diffable_with_correct_args(self, tmp_path: Path) -> None:
        """AC: Calls sqlite-diffable dump with correct arguments."""
        db_path = tmp_path / "test.db"
        db_path.write_text("fake db")
        dump_dir = tmp_path / "dump"

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ) as mock_exec:
            await dump_database(db_path, dump_dir)

        mock_exec.assert_called_once_with(
            "sqlite-diffable",
            "dump",
            str(db_path),
            str(dump_dir),
            "--all",
            stdout=-1,
            stderr=-1,
        )


class TestRestoreDatabase:
    """Tests for restore_database."""

    async def test_deletes_existing_db(self, tmp_path: Path) -> None:
        """AC: Deletes the existing DB file before restoring."""
        db_path = tmp_path / "test.db"
        db_path.write_text("old db content")
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await restore_database(db_path, dump_dir)

        # The file was deleted (sqlite-diffable creates a new one)
        # In mock scenario, the file is just deleted
        assert not db_path.exists() or db_path.read_text() != "old db content"

    async def test_works_when_no_existing_db(self, tmp_path: Path) -> None:
        """AC: Works fine when DB doesn't exist yet."""
        db_path = tmp_path / "test.db"
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ):
            await restore_database(db_path, dump_dir)  # Should not raise

    async def test_raises_on_subprocess_failure(self, tmp_path: Path) -> None:
        """AC: Raises RuntimeError when sqlite-diffable load fails."""
        db_path = tmp_path / "test.db"
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()

        with (
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=AsyncSubprocessMock(
                    returncode=1, stderr=b"Error: corrupt dump"
                ),
            ),
            pytest.raises(RuntimeError, match="sqlite-diffable load"),
        ):
            await restore_database(db_path, dump_dir)

    async def test_calls_sqlite_diffable_with_correct_args(self, tmp_path: Path) -> None:
        """AC: Calls sqlite-diffable load with --replace flag."""
        db_path = tmp_path / "test.db"
        dump_dir = tmp_path / "dump"
        dump_dir.mkdir()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=AsyncSubprocessMock(returncode=0),
        ) as mock_exec:
            await restore_database(db_path, dump_dir)

        mock_exec.assert_called_once_with(
            "sqlite-diffable",
            "load",
            str(db_path),
            str(dump_dir),
            "--replace",
            stdout=-1,
            stderr=-1,
        )
