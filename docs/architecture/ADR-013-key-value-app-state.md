# ADR-013: Key-Value Application State Table

**Status**: Accepted
**Date**: 2026-04-22

## Context

Multiple subsystems need to persist small pieces of internal state that survive restarts — the update checker needs to track the last notified version, the migration system needs to record applied schema versions, and future features will likely need similar lightweight persistence.

Each of these could get its own dedicated table, but that creates schema churn (a new table per feature) for data that is fundamentally the same shape: a key identifying the state and a string value.

The existing persistence layer (ADR-007) uses SQLAlchemy with aiosqlite and a shared `Database` class. Adding a general-purpose table follows the established pattern without introducing a new storage mechanism.

## Decision

Add a single `app_state` table to the existing database with a key-value schema:

```python
class AppStateModel(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=func.now(), onupdate=func.now())
```

Access through a repository with typed get/set methods:

```python
class AppStateRepository:
    async def get(self, key: str) -> str | None
    async def set(self, key: str, value: str) -> None
```

Keys are namespaced by convention using dot-separated prefixes (e.g., `updates.last_notified_version`, `migrations.schema_version`). This keeps the table flat and queryable while preventing collisions.

## Consequences

### Positive

- Single table serves all subsystems — no schema migration needed when new state requirements emerge
- Follows existing SQLAlchemy/async patterns (ADR-007) — no new dependencies or storage mechanisms
- Simple repository interface — get/set by key is all most consumers need
- `updated_at` provides auditability for when state last changed
- Survives restarts and is included in the existing DB dump/restore flow (ADR-012)

### Negative

- Values are untyped strings — consumers must serialize/deserialize themselves (acceptable for small, infrequent reads)
- No foreign keys or relational integrity — keys are just strings, so typos or stale keys are possible
- Not suitable for high-volume or structured data — if a subsystem needs complex queries, it should have its own table

## Alternatives Considered

### Dedicated table per subsystem

- **Description**: Each feature (update checker, migrations) gets its own table with typed columns
- **Why rejected**: Schema churn for data that is fundamentally key-value. A new migration for every new piece of state. Overkill for single-row or few-row tables.

### File-based state under `.tachikoma/state/`

- **Description**: Each piece of state stored as a file (e.g., `.tachikoma/state/last_notified_version`)
- **Why rejected**: Introduces a new persistence mechanism alongside the database. No transactional safety. File I/O for what is essentially a few bytes of state.

### Environment variables / config file

- **Description**: Store runtime state in config or env vars
- **Why rejected**: Config is for user-configurable settings, not internal runtime state. Env vars don't persist across restarts without external tooling.

---

## Notes

- First consumers: update checker (`updates.last_notified_version`), future migration tracking (`migrations.schema_version`)
- Repository lives in `src/tachikoma/app_state.py` — a thin module, not a full subsystem package
- Table creation handled by existing `Base.metadata.create_all()` in the database bootstrap hook
