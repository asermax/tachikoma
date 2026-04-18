"""Tests for the ProcessRecord domain model and ORM row."""

from datetime import UTC, datetime

from tachikoma.detached_processes.model import (
    ProcessRecord,
    ProcessRecordRow,
)


def _make_row(**overrides) -> ProcessRecordRow:
    """Create a ProcessRecordRow with sensible defaults."""
    defaults = {
        "id": "test-id",
        "name": "Test",
        "command": "echo hi",
        "cwd": "/tmp",
        "pid": 999,
        "process_create_time": 1234567.0,
        "log_path": "/tmp/test-id.log",
        "status": "running",
        "started_at": datetime.now(UTC),
        "exited_at": None,
        "exit_code": None,
    }
    defaults.update(overrides)
    return ProcessRecordRow(**defaults)


def test_to_domain_all_nullable_none():
    row = _make_row(exited_at=None, exit_code=None)
    record = row.to_domain()

    assert isinstance(record, ProcessRecord)
    assert record.id == "test-id"
    assert record.name == "Test"
    assert record.command == "echo hi"
    assert record.cwd == "/tmp"
    assert record.pid == 999
    assert record.process_create_time == 1234567.0
    assert record.log_path == "/tmp/test-id.log"
    assert record.status == "running"
    assert record.started_at.tzinfo == UTC
    assert record.exited_at is None
    assert record.exit_code is None


def test_to_domain_all_populated():
    now = datetime.now(UTC)
    row = _make_row(
        status="exited",
        exited_at=now,
        exit_code=42,
    )
    record = row.to_domain()

    assert record.status == "exited"
    assert record.exited_at is not None
    assert record.exited_at.tzinfo == UTC
    assert record.exit_code == 42


def test_round_trip():
    """Domain → row field values → domain preserves all fields."""
    now = datetime.now(UTC)
    original = ProcessRecord(
        id="rt-test",
        name="RoundTrip",
        command="sleep 1",
        cwd="/home",
        pid=4242,
        process_create_time=99999.0,
        log_path="/logs/rt-test.log",
        status="running",
        started_at=now,
        exited_at=None,
        exit_code=None,
    )

    row = ProcessRecordRow(
        id=original.id,
        name=original.name,
        command=original.command,
        cwd=original.cwd,
        pid=original.pid,
        process_create_time=original.process_create_time,
        log_path=original.log_path,
        status=original.status,
        started_at=original.started_at,
        exited_at=original.exited_at,
        exit_code=original.exit_code,
    )

    round_tripped = row.to_domain()

    assert round_tripped.id == original.id
    assert round_tripped.name == original.name
    assert round_tripped.command == original.command
    assert round_tripped.cwd == original.cwd
    assert round_tripped.pid == original.pid
    assert round_tripped.process_create_time == original.process_create_time
    assert round_tripped.log_path == original.log_path
    assert round_tripped.status == original.status
    assert round_tripped.exit_code == original.exit_code
