# Design: Session Tracking

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/agent/sessions.md](../../feature-specs/agent/sessions.md)
**Status**: Current

## Purpose

This document explains the design rationale for the session tracking system: the persistence approach, model/repository/registry layering, crash recovery mechanism, and integration with the coordinator and bootstrap system.

## Problem Context

The system needs a persistent record of conversation sessions so that downstream features (memory extraction, boundary detection) can identify which conversation to analyze and when sessions start and end. The coordinator manages an SDK client session, but nothing persists session metadata across restarts or provides queryable history.

**Constraints:**
- Single-user, single-process deployment — no concurrent writers
- Sessions table will be small (at most thousands of rows after extended use)
- Must integrate with the bootstrap hook system for crash recovery
- Async-first codebase

**Interactions:**
- Coordinator (core-architecture): creates sessions on first message, updates metadata on Result events, closes on shutdown
- Boundary detectors (future): signal session close
- Post-processing pipeline: receives `Session` as input on session close (see [memory-extraction design](../../feature-designs/memory/memory-extraction.md))
- Bootstrap system (workspace-bootstrap): recovery hook runs on startup

## Design Overview

Four components implement session tracking:

```
┌──────────────────────────────────────────────────────────────┐
│                     Coordinator Layer                          │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Coordinator                                           │   │
│  │  ┌──────────────────────┐                              │   │
│  │  │ SessionRegistry      │ create / close / update      │   │
│  │  └──────────┬───────────┘                              │   │
│  └─────────────┼──────────────────────────────────────────┘   │
│                │                                               │
├────────────────┼──────────────────────────────────────────────┤
│                │        Persistence Layer                       │
│                ▼                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  SessionRepository (SQLAlchemy 2.0 async)              │   │
│  │  ┌──────────────────────┐                              │   │
│  │  │  AsyncEngine         │ sqlite+aiosqlite             │   │
│  │  │  async_sessionmaker  │                              │   │
│  │  └──────────┬───────────┘                              │   │
│  └─────────────┼──────────────────────────────────────────┘   │
│                │                                               │
│                ▼                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  sessions.db  (.tachikoma/sessions.db)                 │   │
│  └────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                     Bootstrap Layer                             │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  session_recovery_hook (async)                          │   │
│  │  → registry.recover_interrupted()                       │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

The **SessionRegistry** is the facade that the coordinator calls. It owns the business logic (creation serialization, status derivation, crash recovery) and delegates persistence to the **SessionRepository**. The repository uses SQLAlchemy 2.0's async ORM with `aiosqlite` for the SQLite backend.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/sessions/model.py` | SQLAlchemy ORM models (`SessionRecord`, `SessionResumptionRecord`) + frozen dataclasses (`Session`, `SessionResumption`) + `DeclarativeBase` | Separate ORM models from domain dataclasses; callers never see SQLAlchemy types |
| `src/tachikoma/sessions/repository.py` | `SessionRepository`: async engine lifecycle, CRUD operations, schema creation, time-range queries, `_run_migrations()` for schema evolution (uses `create_all()` for fresh databases plus `pragma_table_info` checks and `ALTER TABLE`/`CREATE TABLE` for existing databases), `get_recent_closed()` for resumption candidates, `create_resumption()`/`get_resumptions_for_session()` for resumption tracking | Owns SQLAlchemy engine + session factory; all SQL is behind async methods |
| `src/tachikoma/sessions/registry.py` | `SessionRegistry`: business logic facade, creation lock, crash recovery, status derivation, `update_summary()` for persisting rolling summaries, `reopen_session()` for session resumption (uses `dataclasses.replace()` to construct reopened session — avoids redundant DB fetch), `get_recent_closed()` for candidate queries, `record_resumption()` for best-effort tracking | Receives repository via constructor; owns the `asyncio.Lock` |
| `src/tachikoma/sessions/errors.py` | `SessionRepositoryError`: wraps SQLAlchemy exceptions for clean error contract | Callers catch one domain exception, not SQLAlchemy internals |
| `src/tachikoma/sessions/hooks.py` | `session_recovery_hook`: creates repository + registry, runs recovery, stores on context extras | Registered as bootstrap hook; runs after workspace hook |
| `src/tachikoma/sessions/__init__.py` | Re-exports public API: `Session`, `SessionResumption`, `SessionRegistry`, `SessionRepository`, `SessionRepositoryError` | Clean public API for the sessions package |

### Cross-Layer Contracts

```mermaid
sequenceDiagram
    actor User
    participant Channel
    participant Coord as Coordinator
    participant Registry as SessionRegistry
    participant Repo as SessionRepository
    participant DB as sessions.db

    Note over Channel,DB: First message in a conversation
    User->>Channel: sends message
    Channel->>Coord: send_message(text)
    Coord->>Registry: create_session()
    Registry->>Repo: create(Session)
    Repo->>DB: INSERT INTO sessions
    Repo-->>Registry: Session
    Registry-->>Coord: Session

    Coord->>Coord: process message via SDK

    Note over Coord,DB: Result event with session metadata
    Coord-->>Channel: yield Result(session_id=...)
    Coord->>Registry: update_metadata(id, sdk_session_id, transcript_path)
    Registry->>Repo: update(id, fields)
    Repo->>DB: UPDATE sessions SET ...

    Note over Coord,DB: Clean shutdown
    Coord->>Registry: close_session(id)
    Registry->>Repo: update(id, ended_at=now)
    Repo->>DB: UPDATE sessions SET ended_at = ...
```

**Integration Points:**
- Coordinator → SessionRegistry: `get_active_session()` + `create_session()` on first message, `update_metadata()` on Result events, `close_session()` on shutdown and topic shift, `get_recent_closed()` for resumption candidates, `reopen_session()` for session resumption, `record_resumption()` for best-effort tracking, `get_by_time_range()` for bridging context assembly
- SummaryProcessor → SessionRegistry: `update_summary()` after each per-message pipeline run (see [boundary detection design](boundary-detection.md))
- SessionRegistry → SessionRepository: all persistence delegated
- SessionRepository → SQLAlchemy AsyncEngine → aiosqlite → sessions.db
- Bootstrap → SessionRegistry: `recover_interrupted()` on startup via `session_recovery_hook`

**Session close mechanism:**

Sessions close via two runtime mechanisms and one startup mechanism:
1. **Boundary detection** (primary, mid-conversation): When a topic shift is detected, the coordinator closes the current session and opens a new one. Post-processing runs as a background task.
2. **Coordinator disconnect** (shutdown safety net): On clean shutdown, the coordinator's `__aexit__` calls `registry.close_session()`, then triggers the post-processing pipeline with the closed session if it has a valid `sdk_session_id`.
3. **Crash recovery** (startup): On next launch, the bootstrap recovery hook closes interrupted sessions.

**Error contract:**

Repository methods raise `SessionRepositoryError` on persistence failures (wrapping the underlying SQLAlchemy exception). The registry propagates these to callers. Session tracking errors in the coordinator are logged but never crash the conversation (graceful degradation).

### Shared Logic

- **`Session` dataclass** (`sessions/model.py`): shared between registry (produces) and future consumers like post-processing pipelines. No SQLAlchemy dependency for consumers.
- **`SessionRepository`** lifecycle: created in the recovery hook, stored on `ctx.extras`, engine disposed in `__main__.py`'s finally block.

## Modeling

### Domain model

```mermaid
erDiagram
    Session ||--o{ SessionResumption : "tracked by"
    Session {
        string id PK "UUID4 hex string"
        string sdk_session_id "nullable - set on Result event"
        string transcript_path "nullable - derived from sdk_session_id"
        string summary "nullable - rolling conversation summary"
        datetime started_at "UTC - set on creation"
        datetime ended_at "nullable UTC - set on close"
        datetime last_resumed_at "nullable UTC - set on reopen"
    }
    SessionResumption {
        int id PK "autoincrement"
        string session_id FK "references sessions.id"
        datetime resumed_at "UTC - when resumption occurred"
        datetime previous_ended_at "UTC - close timestamp before resumption"
    }
```

### Session dataclass (domain representation)

```
Session (frozen dataclass)
├── id: str                           (UUID4 hex, generated at creation)
├── sdk_session_id: str | None        (populated from Result event)
├── transcript_path: str | None       (derived from SDK session ID)
├── summary: str | None               (rolling conversation summary, updated by per-message pipeline)
├── started_at: datetime              (UTC, set at creation time)
├── ended_at: datetime | None         (UTC, set when session closes; cleared on reopen)
├── last_resumed_at: datetime | None  (UTC, set when session is reopened for resumption)
└── status: SessionStatus (property)  (derived, not persisted)
    ├── "open"        — ended_at is None
    ├── "closed"      — ended_at is set AND sdk_session_id is set
    └── "interrupted" — ended_at is set AND sdk_session_id is None

SessionResumption (frozen dataclass)
├── session_id: str                   (FK → sessions.id)
├── resumed_at: datetime              (UTC, when resumption occurred)
└── previous_ended_at: datetime       (UTC, close timestamp before this resumption)
```

`SessionStatus` is a `Literal["open", "closed", "interrupted"]` type.

### SQLAlchemy ORM model

```
SessionRecord (DeclarativeBase)
├── __tablename__ = "sessions"
├── id: Mapped[str]                   (primary_key=True)
├── sdk_session_id: Mapped[str | None]
├── transcript_path: Mapped[str | None]
├── summary: Mapped[str | None]       (rolling conversation summary)
├── started_at: Mapped[datetime]      (DateTime(timezone=True))
├── ended_at: Mapped[datetime | None] (DateTime(timezone=True))
├── last_resumed_at: Mapped[datetime | None] (DateTime(timezone=True))
└── index on started_at               (for time-range queries)

SessionResumptionRecord (DeclarativeBase)
├── __tablename__ = "session_resumptions"
├── id: Mapped[int]                   (primary_key=True, autoincrement)
├── session_id: Mapped[str]           (ForeignKey("sessions.id"))
├── resumed_at: Mapped[datetime]      (DateTime(timezone=True))
├── previous_ended_at: Mapped[datetime] (DateTime(timezone=True))
└── index on session_id
```

The ORM models are internal to the persistence layer. A `to_domain()` method on each record converts to the frozen dataclass. The registry and all callers work exclusively with domain instances.

## Data Flow

### Session creation (first message)

```
1. Coordinator receives first message of a conversation
2. Coordinator checks registry.get_active_session() — returns None
3. Coordinator calls registry.create_session()
4. Registry acquires asyncio.Lock
5. Registry generates UUID4 hex string as session ID
6. Registry creates Session(id=..., started_at=utcnow(), ...)
7. Registry calls repository.create(session)
8. Repository opens AsyncSession, adds SessionRecord, commits
9. Registry releases lock, returns Session to coordinator
10. Coordinator proceeds with send_message()
```

### Session metadata update (on Result event)

```
1. Coordinator receives Result event with session_id from SDK
2. Coordinator derives transcript_path from SDK session ID
3. Coordinator calls registry.update_metadata(session_id, sdk_session_id, transcript_path)
4. Registry calls repository.update(id, sdk_session_id=..., transcript_path=...)
5. Repository queries by ID, updates fields, commits
```

The `transcript_path` is derived from the SDK session ID using the known Claude SDK directory structure: `~/.claude/projects/<sanitized-cwd>/<session-id>.jsonl`, where `<sanitized-cwd>` replaces `/` with `-` and strips the leading `-`. This derivation is isolated to a single helper function (`_derive_transcript_path` in the coordinator) so it can be updated in one place.

### Session close (shutdown)

```
1. Coordinator __aexit__ triggers (clean shutdown or exception)
2. Coordinator checks for active session via registry
3. Coordinator calls registry.close_session(id) — errors logged, not propagated
4. Registry calls repository.update(id, ended_at=utcnow())
5. Idempotent: already-closed sessions are no-ops
```

### Crash recovery (bootstrap)

```
1. session_recovery_hook runs (after workspace_hook)
2. Creates SessionRepository(data_path / "sessions.db")
3. await repository.initialize() — creates engine, session factory, and runs
   _run_migrations() which handles both fresh and existing databases:
   - create_all() for fresh databases (creates all tables from ORM metadata)
   - pragma_table_info checks + ALTER TABLE for column additions on existing databases
   - sqlite_master checks + CREATE TABLE for new tables on existing databases
4. Creates SessionRegistry(repository)
5. Calls registry.recover_interrupted():
   a. Queries open sessions (ended_at IS NULL)
   b. For each: sets ended_at from transcript file mtime (if available) or current time
6. Stores repository + registry on ctx.extras for __main__.py retrieval
```

### Summary update (per-message pipeline)

```
1. SummaryProcessor completes, calls registry.update_summary(session_id, summary)
2. Registry calls repository.update(session_id, summary=...)
3. Repository queries by ID, updates summary field, commits
4. Registry re-fetches session via repository.get_by_id()
5. Registry replaces _active_session with new frozen Session instance
   (same re-fetch-and-replace pattern as update_metadata())
```

### Session reopen (resumption)

```
1. Coordinator calls registry.reopen_session(session_id)
2. Registry fetches session via repository.get_by_id()
3. Registry validates: exists, is closed (ended_at not None), is not already active
4. If invalid: log warning, return None
5. Registry calls repository.update(id, ended_at=None, last_resumed_at=now)
6. Registry constructs reopened Session via dataclasses.replace()
   (avoids a second DB fetch since all field values are known)
7. Registry sets _active_session = reopened
8. Returns reopened Session
```

### Recent sessions query (resumption candidates)

```
1. Coordinator calls registry.get_recent_closed(before=now, window=timedelta)
2. Registry delegates to repository.get_recent_closed(before, window)
3. Repository queries:
   SELECT * FROM sessions
   WHERE ended_at IS NOT NULL
     AND sdk_session_id IS NOT NULL
     AND summary IS NOT NULL
     AND ended_at > (before - window)
   ORDER BY ended_at DESC
4. Returns list of Session dataclass instances
```

### Resumption tracking

```
1. Coordinator calls registry.record_resumption(session_id, previous_ended_at)
2. Registry creates SessionResumption(session_id, resumed_at=now, previous_ended_at)
3. Registry calls repository.create_resumption(resumption)
4. Repository persists SessionResumptionRecord, commits
5. On failure: error logged, not raised (best-effort per R7)
```

### Query by time range

```
1. Caller provides (start, end) datetime range
2. Registry calls repository.get_by_time_range(start, end)
3. Repository queries:
   SELECT * FROM sessions
   WHERE started_at < :range_end
     AND (ended_at IS NULL OR ended_at > :range_start)
   ORDER BY started_at DESC
4. Open sessions (ended_at IS NULL) are included if started_at < range_end
5. Returns list of Session dataclass instances
```

## Key Decisions

### SQLAlchemy 2.0 async over raw aiosqlite

**Choice**: Use SQLAlchemy 2.0 with async ORM and `aiosqlite` backend (see ADR-007)
**Why**: Provides typed ORM models with `Mapped[T]` columns, built-in schema creation, and establishes a persistence pattern for future tables. SQLAlchemy is the industry standard with robust async support and good type hints in 2.0.
**Alternatives Considered**:
- Raw aiosqlite: Lighter but no ORM benefits
- Tortoise ORM: Global init pattern, extra dependencies
- SQLModel: Async SQLite path under-documented

**Consequences**:
- Pro: Typed ORM model, built-in schema management, established pattern
- Pro: Scales naturally if more tables are added
- Con: Heavier dependency than raw aiosqlite for a single-table use case

### Separate ORM model from domain dataclass

**Choice**: `SessionRecord` (SQLAlchemy ORM) is internal to the persistence layer; callers receive frozen `Session` dataclasses
**Why**: Prevents SQLAlchemy types from leaking into the coordinator, boundary detectors, and post-processing pipelines. Consistent with the adapter pattern used for SDK messages (AgentEvent).

**Consequences**:
- Pro: Consumers never import SQLAlchemy
- Pro: Domain model is a plain frozen dataclass — easy to test, serialize, inspect
- Con: Requires a `to_domain()` mapping step in the repository

### Derived session status (not persisted)

**Choice**: Session status (`open`/`closed`/`interrupted`) is a computed property on the `Session` dataclass, not a database column
**Why**: Status is fully derivable from `ended_at` and `sdk_session_id`. Storing it would create a synchronization risk.

**Consequences**:
- Pro: No stale status — always consistent with underlying fields
- Pro: Simpler schema — fewer columns to maintain
- Con: Cannot query by status directly in SQL

### UUID4 hex string for session IDs

**Choice**: Use `uuid.uuid4().hex` (32-character hex string) as session IDs
**Why**: Universally unique without coordination. Compact, URL-safe, works as a plain string primary key in SQLite.

**Consequences**:
- Pro: No ID collision risk, no sequence coordination
- Pro: Meaningful as a standalone identifier for logs and cross-referencing

### Sessions as a package

**Choice**: Organize session-related code as `src/tachikoma/sessions/` package with separate modules
**Why**: Three distinct concerns (domain model, persistence, business logic facade) benefit from separate modules for clarity and independent testing.

**Consequences**:
- Pro: Clear separation of concerns, easy to navigate
- Pro: Each module can be tested independently

## System Behavior

### Scenario: First message creates a session

**Given**: No active session exists
**When**: The coordinator receives the first message
**Then**: `registry.create_session()` generates a new session with a UUID4 ID and UTC timestamp. The coordinator proceeds to process the message.
**Rationale**: Sessions are created eagerly on first message, before the SDK processes the request, so that even if the SDK call fails, the session start is recorded.

### Scenario: Result event populates metadata

**Given**: An active session with null SDK metadata
**When**: The coordinator receives a `Result` event with a `session_id`
**Then**: `registry.update_metadata()` sets `sdk_session_id` and derives `transcript_path`.
**Rationale**: The SDK assigns its own session ID internally; the registry captures it on the first Result event for cross-referencing with SDK transcripts.

### Scenario: Clean shutdown closes active session

**Given**: An active session exists
**When**: The coordinator exits its async context
**Then**: `registry.close_session()` sets `ended_at`. Errors are logged but not propagated.
**Rationale**: Clean session close enables time-range queries and signals readiness for post-processing.

### Scenario: Close on already-closed session (idempotent)

**Given**: A session with `ended_at` already set
**When**: A close signal is received again
**Then**: The operation completes without error or change.
**Rationale**: Multiple close sources may fire redundantly; idempotency prevents errors.

### Scenario: Crash recovery on startup

**Given**: The application crashed leaving sessions with null `ended_at`
**When**: The recovery hook runs
**Then**: Open sessions are closed with best-effort timestamps (transcript file mtime if available, otherwise current time).
**Rationale**: Best-effort timestamps are more accurate than "now" when the crash happened some time ago.

### Scenario: Recovery with no open sessions (idempotent)

**Given**: All sessions have `ended_at` set
**When**: The recovery hook runs
**Then**: No changes are made. The hook completes silently.
**Rationale**: Idempotent — safe to run on every launch.

### Scenario: Session reopened for resumption

**Given**: A closed session with `sdk_session_id` and `ended_at` set
**When**: The coordinator calls `reopen_session()` with the session ID
**Then**: `ended_at` is cleared, `last_resumed_at` is set to now, the session becomes the active session. The registry constructs the reopened session via `dataclasses.replace()` without a redundant DB fetch.

### Scenario: Reopen fails — session not found

**Given**: A session ID that doesn't exist in the database
**When**: `reopen_session()` is called
**Then**: A warning is logged and None is returned. The coordinator falls back to fresh-session behavior.

### Scenario: Resumption tracking recorded

**Given**: A session was successfully reopened
**When**: `record_resumption()` is called
**Then**: A `SessionResumption` record is persisted with the session ID, current timestamp, and previous close timestamp.

### Scenario: Resumption tracking fails gracefully

**Given**: A session was successfully reopened
**When**: `record_resumption()` encounters a database error
**Then**: The error is logged but the session remains resumed — tracking is best-effort.

### Scenario: Session resumed, closed, then resumed again

**Given**: A session that was previously resumed and then closed again
**When**: `reopen_session()` is called again
**Then**: `ended_at` is cleared, `last_resumed_at` is updated to the new timestamp. A second `SessionResumption` record is created.

### Scenario: Session tracking failure during conversation

**Given**: A conversation is active and a registry method fails
**When**: The coordinator catches the error
**Then**: The error is logged and the conversation continues uninterrupted.
**Rationale**: Session tracking is important but not critical to message processing. Graceful degradation is preferred.

## Notes

- The sessions package structure sets a precedent for future persistence needs (e.g., tasks could follow the same model/repository/registry pattern)
- `expire_on_commit=False` is used on the `async_sessionmaker` to allow attribute access on `SessionRecord` instances after commit (before `to_domain()` conversion)
- SQLite stores datetimes as naive ISO strings; a `_ensure_utc()` helper restores UTC tzinfo on read so callers always receive timezone-aware datetimes
- The `BootstrapContext.extras` field is used to pass the repository and registry from the recovery hook to `__main__.py` — see [workspace-bootstrap design](workspace-bootstrap.md)
