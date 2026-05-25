"""ProcessRepository: async SQLAlchemy persistence for detached process records.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

from datetime import datetime

from loguru import logger
from sqlalchemy import delete as sql_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.detached_processes.errors import ProcessRepositoryError
from tachikoma.detached_processes.model import (
    STOP_REASON_AGENT_STOPPED,
    ProcessRecord,
    ProcessRecordRow,
    ProcessStatus,
)

_log = logger.bind(component="detached_processes")


class ProcessRepository:
    """Async repository for detached process records backed by SQLite via aiosqlite.

    Receives a shared session factory from the Database class.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create(self, record: ProcessRecord) -> ProcessRecord:
        """Persist a new process record and return it."""
        try:
            row = ProcessRecordRow(
                id=record.id,
                name=record.name,
                command=record.command,
                cwd=record.cwd,
                pid=record.pid,
                process_create_time=record.process_create_time,
                log_path=record.log_path,
                status=record.status,
                started_at=record.started_at,
                exited_at=record.exited_at,
                exit_code=record.exit_code,
                stop_reason=record.stop_reason,
                memory_limit=record.memory_limit,
                cgroup_path=record.cgroup_path,
            )

            async with self._session_factory() as db:
                db.add(row)
                await db.commit()

            return row.to_domain()

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to create process record {record.id}") from exc

    async def get(self, record_id: str) -> ProcessRecord | None:
        """Return the process record with the given ID, or None if not found."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.id == record_id)
                )
                row = result.scalar_one_or_none()

            return row.to_domain() if row is not None else None

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to get process record {record_id}") from exc

    async def list_by_status(self, status: ProcessStatus) -> list[ProcessRecord]:
        """Return all process records with the given status."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.status == status)
                )
                rows = result.scalars().all()

            return [r.to_domain() for r in rows]

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to list {status} processes") from exc

    async def list_running(self) -> list[ProcessRecord]:
        """Return all process records with status='running'."""
        return await self.list_by_status("running")

    async def list_exited(self) -> list[ProcessRecord]:
        """Return all process records with status='exited'."""
        return await self.list_by_status("exited")

    async def rename(self, record_id: str, name: str) -> None:
        """Rename a process record. No-op if the record does not exist."""
        try:
            async with self._session_factory() as db:
                await db.execute(
                    update(ProcessRecordRow)
                    .where(ProcessRecordRow.id == record_id)
                    .values(name=name)
                )
                await db.commit()

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to rename process record {record_id}") from exc

    async def _update_stop_reason(self, record_id: str, value: str | None) -> None:
        try:
            async with self._session_factory() as db:
                await db.execute(
                    update(ProcessRecordRow)
                    .where(ProcessRecordRow.id == record_id)
                    .values(stop_reason=value)
                )
                await db.commit()

        except Exception as exc:
            raise ProcessRepositoryError(
                f"Failed to update stop reason for process record {record_id}"
            ) from exc

    async def mark_stop_initiated(self, record_id: str) -> None:
        """Set stop_reason='agent_stopped' before signalling.

        Caller must clear_stop_reason if signal delivery fails, so a
        future natural exit is not incorrectly suppressed.
        """
        await self._update_stop_reason(record_id, STOP_REASON_AGENT_STOPPED)

    async def clear_stop_reason(self, record_id: str) -> None:
        """Clear stop_reason after a failed signal delivery."""
        await self._update_stop_reason(record_id, None)

    async def delete(self, record_id: str) -> bool:
        """Delete a process record by ID. Returns True if deleted."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    sql_delete(ProcessRecordRow).where(ProcessRecordRow.id == record_id)
                )
                await db.commit()

            return result.rowcount > 0

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to delete process record {record_id}") from exc

    async def reconcile_to_exited(
        self,
        record_id: str,
        *,
        exited_at: datetime,
        exit_code: int | None,
    ) -> bool:
        """Conditionally transition a record from running to exited.

        Issues a single UPDATE ... WHERE id=:id AND status='running'.
        Returns True if the row was updated (this caller won the race),
        False if no row matched (another caller already reconciled).
        """
        try:
            async with self._session_factory() as db:
                stmt = (
                    update(ProcessRecordRow)
                    .where(
                        ProcessRecordRow.id == record_id,
                        ProcessRecordRow.status == "running",
                    )
                    .values(
                        status="exited",
                        exited_at=exited_at,
                        exit_code=exit_code,
                    )
                )
                result = await db.execute(stmt)
                await db.commit()

            return result.rowcount > 0

        except Exception as exc:
            raise ProcessRepositoryError(
                f"Failed to reconcile process record {record_id} to exited"
            ) from exc
