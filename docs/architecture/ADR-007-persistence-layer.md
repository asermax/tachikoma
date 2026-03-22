# ADR-007: Persistence Layer

**Status**: Accepted
**Date**: 2026-03-12
**Last Updated**: 2026-03-12

## Context

Tachikoma needs persistent storage with structured query capabilities for conversation session tracking and future features (task queues, memory metadata). The project vision states "file-based — no database for v1," intended to avoid server-process databases. Session tracking requires time-range queries, ID lookups, and indexed fields that flat-file formats (JSON, CSV) cannot offer efficiently.

The codebase is async-first, running on a single-process, single-user deployment. The persistence layer must integrate cleanly with the existing async architecture.

## Decision

Use **SQLAlchemy 2.0 with async ORM** and the **aiosqlite** backend for all persistence needs.

SQLite is an embedded file-based database with no server process, satisfying the spirit of the "no database" constraint. SQLAlchemy 2.0 provides typed ORM models (`Mapped[T]`), built-in schema creation (`Base.metadata.create_all`), and a mature async extension via `create_async_engine` and `async_sessionmaker`.

The pattern established:
- ORM models (`DeclarativeBase` subclasses) are internal to the persistence layer
- Callers receive frozen dataclasses (domain types) — no SQLAlchemy imports leak
- Each persistent feature organizes as a package: `model.py`, `repository.py`, `registry.py`
- All subsystems share a single `Database` class (`src/tachikoma/database.py`) with one `Base(DeclarativeBase)`, one `AsyncEngine`, and one `async_sessionmaker` backed by `tachikoma.db`
- Repositories receive the shared `session_factory` via dependency injection
- Engine lifecycle: `database_hook` creates the shared `Database` in bootstrap, `__main__.py`'s finally block calls `database.close()`

## Consequences

### Positive

- Typed ORM models with column-level type hints (`Mapped[str]`, `Mapped[datetime | None]`)
- Built-in schema management — `create_all()` handles table creation without separate migration tooling
- Established pattern for future persistence (task queues, memory metadata can follow the same model/repository structure)
- Familiar ecosystem — extensive documentation, community support, well-understood by the developer
- Async-native — `create_async_engine` + `aiosqlite` integrates cleanly with the existing async architecture

### Negative

- Heavier dependency than raw aiosqlite for what starts as a single-table use case
- Requires explicit engine disposal on shutdown to prevent resource leaks
- `Base.metadata.create_all` is synchronous — requires `run_sync` bridge inside async engine

## Alternatives Considered

### Raw aiosqlite

- **Description**: Use aiosqlite directly with hand-written SQL queries
- **Why rejected**: No ORM benefits (no typed models, no schema creation, manual SQL for every operation). Suitable for the simplest cases but doesn't scale as tables are added.

### Tortoise ORM

- **Description**: Async ORM inspired by Django's, with native async support
- **Why rejected**: Pulls in 4 transitive dependencies. Uses a global initialization pattern (`Tortoise.init()`) that conflicts with the project's explicit wiring style. Less established ecosystem than SQLAlchemy.

### SQLModel

- **Description**: Pydantic + SQLAlchemy hybrid by the FastAPI author
- **Why rejected**: Async SQLite path is under-documented. Adds Pydantic as a dependency for ORM modeling when we already have plain dataclasses. The hybrid model/schema design adds complexity without clear benefit for this use case.

### Peewee with peewee-aio

- **Description**: Lightweight ORM with third-party async adapter
- **Why rejected**: Async SQLite story is fragmented across multiple third-party libraries (peewee-aio, peewee-async). Less robust async support than SQLAlchemy 2.0's first-party extension.

---

## Notes

- SQLAlchemy 2.0 asyncio docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- aiosqlite: https://github.com/omnilib/aiosqlite
- VISION.md updated to clarify "file-based" acknowledges SQLite as an embedded file-based database
