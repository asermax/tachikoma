"""ProcessRepository: async SQLAlchemy persistence for detached process records.

All callers receive frozen dataclasses — SQLAlchemy types never leak out
of this module.
"""

from datetime import datetime

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from tachikoma.detached_processes.errors import ProcessRepositoryError
from tachikoma.detached_processes.model import (
    ProcessRecord,
    ProcessRecordRow,
)

_log = logger.bind(component="detached_processes")


class ProcessRepository:
    """Async repository for detached process records backed by SQLite via aiosqlite.

    Receives a shared session factory from the Database class.

    Usage::

        repo = ProcessRepository(database.session_factory)
        record = await repo.create(record_obj)
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

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

    async def list_running(self) -> list[ProcessRecord]:
        """Return all process records with status='running'."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.status == "running")
                )
                rows = result.scalars().all()

            return [r.to_domain() for r in rows]

        except Exception as exc:
            raise ProcessRepositoryError("Failed to list running processes") from exc

    async def list_exited(self) -> list[ProcessRecord]:
        """Return all process records with status='exited'."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.status == "exited")
                )
                rows = result.scalars().all()

            return [r.to_domain() for r in rows]

        except Exception as exc:
            raise ProcessRepositoryError("Failed to list exited processes") from exc

    async def update(self, record_id: str, **fields) -> None:
        """Update arbitrary fields on a process record by ID.

        Accepted fields: name, status, exited_at, exit_code.
        """
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.id == record_id)
                )
                row = result.scalar_one_or_none()

                if row is None:
                    return

                for key, value in fields.items():
                    setattr(row, key, value)

                await db.commit()

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to update process record {record_id}") from exc

    async def delete(self, record_id: str) -> bool:
        """Delete a process record by ID. Returns True if deleted."""
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(ProcessRecordRow).where(ProcessRecordRow.id == record_id)
                )
                row = result.scalar_one_or_none()

                if row is None:
                    return False

                await db.delete(row)
                await db.commit()

            return True

        except Exception as exc:
            raise ProcessRepositoryError(f"Failed to delete process record {record_id}") from exc

    # ------------------------------------------------------------------
    # Race-safe conditional update
    # ------------------------------------------------------------------

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
