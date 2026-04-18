"""Shared fixtures for detached process tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tachikoma.database import Database
from tachikoma.detached_processes.model import ProcessRecord
from tachikoma.detached_processes.repository import ProcessRepository


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _make_record(
    record_id: str = "test-proc",
    name: str = "Test Process",
    command: str = "sleep 10",
    cwd: str = "/tmp",
    pid: int = 12345,
    process_create_time: float = 1000000.0,
    log_path: str = "/tmp/test-proc.log",
    status: str = "running",
    started_at: datetime | None = None,
    exited_at: datetime | None = None,
    exit_code: int | None = None,
) -> ProcessRecord:
    """Create a ProcessRecord with sensible defaults."""
    return ProcessRecord(
        id=record_id,
        name=name,
        command=command,
        cwd=cwd,
        pid=pid,
        process_create_time=process_create_time,
        log_path=log_path,
        status=status,  # type: ignore[arg-type]
        started_at=started_at or _utcnow(),
        exited_at=exited_at,
        exit_code=exit_code,
    )


@pytest.fixture
async def repo(tmp_path: Path) -> ProcessRepository:
    """Initialized ProcessRepository backed by a temp SQLite file."""
    database = Database(tmp_path / "tachikoma.db")
    await database.initialize()
    yield ProcessRepository(database.session_factory)
    await database.close()
