"""Process domain model and SQLAlchemy ORM models.

Keeps the ORM model (ProcessRecordRow) internal to the persistence layer.
Callers work exclusively with the frozen ProcessRecord dataclass.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tachikoma.database import Base
from tachikoma.db_utils import ensure_utc

ProcessStatus = Literal["running", "exited"]


@dataclass(frozen=True)
class ProcessRecord:
    """Domain representation of a detached process record.

    Returned to all callers; has no SQLAlchemy dependency.
    """

    id: str
    name: str
    command: str
    cwd: str
    pid: int
    process_create_time: float
    log_path: str
    status: ProcessStatus
    started_at: datetime
    exited_at: datetime | None = None
    exit_code: int | None = None


class ProcessRecordRow(Base):
    """SQLAlchemy ORM model for the detached_processes table.

    Internal to the persistence layer; use to_domain() to convert to the
    ProcessRecord dataclass.
    """

    __tablename__ = "detached_processes"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    command: Mapped[str] = mapped_column(String, nullable=False)
    cwd: Mapped[str] = mapped_column(String, nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    process_create_time: Mapped[float] = mapped_column(Float, nullable=False)
    log_path: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (Index("ix_detached_processes_status", "status"),)

    def to_domain(self) -> ProcessRecord:
        return ProcessRecord(
            id=self.id,
            name=self.name,
            command=self.command,
            cwd=self.cwd,
            pid=self.pid,
            process_create_time=self.process_create_time,
            log_path=self.log_path,
            status=self.status,  # type: ignore[arg-type]
            started_at=ensure_utc(self.started_at),  # type: ignore[arg-type]
            exited_at=ensure_utc(self.exited_at),
            exit_code=self.exit_code,
        )
