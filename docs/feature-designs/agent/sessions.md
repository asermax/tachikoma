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
│  │  SessionRepository (receives shared session_factory)   │   │
│  └─────────────┬──────────────────────────────────────────┘   │
│                │                                               │
│                ▼                                               │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  Database (shared AsyncEngine + async_sessionmaker)    │   │
│  │  → tachikoma.db (.tachikoma/tachikoma.db)              │   │
│  └────────────────────────────────────────────────────────┘   │
├──────────────────────────────────────────────────────────────┤
│                     Bootstrap Layer                             │
│  ┌────────────────────────────────────────────────────────┐   │
│  │  database_hook (async) → creates shared Database       │   │
│  │  session_recovery_hook (async)                          │   │
│  │  → creates SessionRepository(database.session_factory)  │   │
│  │  → registry.recover_interrupted()                       │   │
│  └────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

The **SessionRegistry** is the facade that the coordinator calls. It owns the business logic (creation serialization, status derivation, crash recovery) and delegates persistence to the **SessionRepository**. The repository uses SQLAlchemy 2.0's async ORM with `aiosqlite` for the SQLite backend.

The session domain also includes **SessionContextEntry** — a persisted record of each context entry injected into a session's system prompt. Entries are saved at lifecycle points (session creation, pre-processing, boundary detection) and loaded by the coordinator for system prompt assembly. Context entry persistence follows the same layered pattern: `SessionRepository` provides CRUD, `SessionRegistry` exposes a facade.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/sessions/model.py` | SQLAlchemy ORM models (`SessionRecord`, `SessionResumptionRecord`, `SessionContextEntryRecord`) + frozen dataclasses (`Session`, `SessionResumption`, `SessionContextEntry`) + `DeclarativeBase` | Separate ORM models from domain dataclasses; callers never see SQLAlchemy types |
| `src/tachikoma/sessions/repository.py` | `SessionRepository`: CRUD operations, time-range queries, `get_recent_closed()` for resumption candidates, `create_resumption()`/`get_resumptions_for_session()` for resumption tracking, `save_context_entries()`/`load_context_entries()` for context entry persistence | Receives shared `async_sessionmaker` from `Database`; all SQL is behind async methods; `save_context_entries` takes list of `(owner, content)` tuples, bulk saves via `session.add_all()`; `load_context_entries` ordered by PK ascending |
| `src/tachikoma/sessions/registry.py` | `SessionRegistry`: business logic facade, creation lock, crash recovery, status derivation, `close_session()` returns `bool` (True if actually transitioned from open to closed — enables callers to distinguish real closes from no-ops), `update_summary()` for persisting rolling summaries, `reopen_session()` for session resumption (uses `dataclasses.replace()` to construct reopened session — avoids redundant DB fetch), `get_recent_closed()` for candidate queries, `record_resumption()` for best-effort tracking, `save_context_entries()` (best-effort — logs on failure per R9/R17, does not raise), `load_context_entries()` (raises on failure — caller handles graceful degradation) | Receives repository via constructor; owns the `asyncio.Lock` |
| `src/tachikoma/sessions/errors.py` | `SessionRepositoryError`: wraps SQLAlchemy exceptions for clean error contract | Callers catch one domain exception, not SQLAlchemy internals |
| `src/tachikoma/sessions/hooks.py` | `session_recovery_hook`: retrieves shared `Database` from extras, creates repository + registry, runs recovery, stores on context extras | Registered as bootstrap hook; runs after `database_hook` |
| `src/tachikoma/sessions/__init__.py` | Re-exports public API: `Session`, `SessionContextEntry`, `SessionResumption`, `SessionRegistry`, `SessionRepository`, `SessionRepositoryError` | Clean public API for the sessions package |

### Cross-Layer Contracts

```mermaid
sequenceDiagram
    actor User
    participant Channel
    participant Coord as Coordinator
    participant Registry as SessionRegistry
    participant Repo as SessionRepository
    participant DB as tachikoma.db

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
- Coordinator → SessionRegistry: `get_active_session()` + `create_session()` on first message, `update_metadata()` on Result events, `close_session()` on shutdown, topic shift, and idle timeout, `get_recent_closed()` for resumption candidates, `reopen_session()` for session resumption, `record_resumption()` for best-effort tracking, `get_by_time_range()` for bridging context assembly, `save_context_entries(session_id, entries)` for persisting context (always takes a list of (owner, content) tuples), `load_context_entries(session_id)` for loading entries for system prompt assembly
- SummaryProcessor → SessionRegistry: `update_summary()` after each per-message pipeline run (see [boundary detection design](boundary-detection.md))
- SessionRegistry → SessionRepository: all persistence delegated
- SessionRepository → shared Database (AsyncEngine → aiosqlite → tachikoma.db)
- Bootstrap → SessionRegistry: `recover_interrupted()` on startup via `session_recovery_hook`

**Session close mechanism:**

Sessions close via three runtime mechanisms and one startup mechanism:
1. **Boundary detection** (primary, mid-conversation): When a topic shift is detected, the coordinator closes the current session and opens a new one. Post-processing runs as a background task.
2. **Idle timeout** (secondary, trails-off conversations): A periodic check (every 60s) closes the active session after a configurable period of inactivity (`session_idle_timeout`, default 900s). If the coordinator is busy (message exchange active, messages queued, or per-message post-processing in flight), the close is snoozed and retried. Unlike boundary detection, idle close does NOT create a new session — the next user message follows the normal first-message path.
3. **Coordinator disconnect** (shutdown safety net): On clean shutdown, the coordinator's `__aexit__` cancels the idle close loop first (preventing a race condition), then calls `registry.close_session()` and triggers post-processing.
4. **Crash recovery** (startup): On next launch, the bootstrap recovery hook closes interrupted sessions.

**Error contract:**

Repository methods raise `SessionRepositoryError` on persistence failures (wrapping the underlying SQLAlchemy exception). The registry propagates these to callers. Session tracking errors in the coordinator are logged but never crash the conversation (graceful degradation).

### Shared Logic

- **`Session` dataclass** (`sessions/model.py`): shared between registry (produces) and future consumers like post-processing pipelines. No SQLAlchemy dependency for consumers.
- **`SessionRepository`** lifecycle: created in the recovery hook with the shared `database.session_factory`, stored on `ctx.extras`. The shared `Database` engine is disposed in `__main__.py`'s finally block.

## Modeling

### Domain model

```mermaid
erDiagram
    Session ||--o{ SessionResumption : "tracked by"
    Session ||--o{ SessionContextEntry : "has context"
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
    SessionContextEntry {
        int id PK "autoincrement - insertion order"
        string session_id FK "references sessions.id"
        string owner "context source identifier"
        string content "text content at injection time"
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

SessionContextEntry (frozen dataclass)
├── id: int                           (autoincrement PK, determines assembly order)
├── session_id: str                   (FK → sessions.id)
├── owner: str                        (context source identifier: soul, user, agents, memories, etc.)
└── content: str                      (text content at time of injection)
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

SessionContextEntryRecord (DeclarativeBase)
├── __tablename__ = "session_context_entries"
├── id: Mapped[int]                   (primary_key=True, autoincrement)
├── session_id: Mapped[str]           (ForeignKey("sessions.id"))
├── owner: Mapped[str]
├── content: Mapped[str]
└── index on session_id               (for load-by-session queries)
```

The ORM models are internal to the persistence layer. A `to_domain()` method on each record converts to the frozen dataclass. The registry and all callers work exclusively with domain instances. The `session_context_entries` table is created via a pragma-check migration in `Database._run_migrations()`, consistent with the `session_resumptions` migration pattern.

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

### Session close (idle timeout)

```
1. Coordinator's idle close loop detects inactivity exceeding session_idle_timeout
2. Loop verifies coordinator is not busy (no active exchange, no queued messages, no pending post-processing)
3. Coordinator calls registry.get_active_session()
4. Coordinator calls registry.close_session(id) — returns bool; errors logged, not propagated
5. If close_session returned True AND session has sdk_session_id: fires async post-processing as background task
6. Coordinator clears SDK state and stores previous summary
7. No new session created — next user message follows first-message path
```

Note: The boolean return from `close_session()` prevents the idle loop from re-firing post-processing on stale sessions. If the session was already closed (idempotent path), `close_session` returns False and clears the active session reference, so `get_active_session()` returns None on the next loop iteration.

### Crash recovery (bootstrap)

```
1. database_hook runs (after workspace_hook) — creates shared Database
2. session_recovery_hook runs (after database_hook)
3. Retrieves Database from ctx.extras["database"]
4. Creates SessionRepository(database.session_factory)
5. Creates SessionRegistry(repository)
6. Calls registry.recover_interrupted():
   a. Queries open sessions (ended_at IS NULL)
   b. For each: sets ended_at from transcript file mtime (if available) or current time
7. Stores repository + registry on ctx.extras for __main__.py retrieval
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

### Context entry persistence

```
1. Coordinator saves entries at lifecycle points via registry.save_context_entries(session_id, entries)
   - Foundational: after session creation (soul, user, agents — one entry per file)
   - Pre-processing: after pipeline completes (one entry per successful provider)
   - Transition: in _handle_transition (previous-summary or bridging-context)
2. Registry delegates to repository.save_context_entries(session_id, entries)
3. Repository creates SessionContextEntryRecord for each (owner, content) tuple
4. Bulk save via session.add_all(), commit
5. On failure: registry's save_context_entries logs warning but doesn't raise (best-effort)
```

### Context entry loading

```
1. Coordinator calls registry.load_context_entries(session_id) before each client creation
2. Registry delegates to repository.load_context_entries(session_id)
3. Repository SELECT ... WHERE session_id = ? ORDER BY id ASC
4. Returns list[SessionContextEntry] via to_domain()
5. On failure: SessionRepositoryError propagates to caller
   (coordinator handles graceful degradation by falling back to base preamble only)
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

### Scenario: Idle timeout closes session

**Given**: An active session with no message exchange for longer than `session_idle_timeout`
**When**: The idle close loop detects the timeout and the coordinator is not busy
**Then**: `registry.close_session()` sets `ended_at`. If the session has a valid `sdk_session_id`, async post-processing is triggered. SDK state is cleared and the summary is stored. No new session is created — the next user message follows the first-message path.
**Rationale**: Conversations that trail off (user stops messaging without changing topics) get their post-processing triggered automatically without requiring a topic shift or restart.

### Scenario: Clean shutdown closes active session

**Given**: An active session exists
**When**: The coordinator exits its async context
**Then**: The idle close loop is cancelled first (if running), then `registry.close_session()` sets `ended_at`. If idle close already closed the session, no active session is found and the close is skipped. Errors are logged but not propagated.
**Rationale**: Clean session close enables time-range queries and signals readiness for post-processing. Cancelling the idle loop first prevents a race between idle close and shutdown close.

### Scenario: Close on already-closed session (idempotent)

**Given**: A session with `ended_at` already set and `_active_session` still referencing it
**When**: A close signal is received again
**Then**: `_active_session` is cleared (so `get_active_session()` returns None), no database update occurs, and `close_session` returns False.
**Rationale**: Multiple close sources may fire redundantly; idempotency prevents errors. Clearing `_active_session` on the idempotent path prevents the idle close loop from repeatedly firing post-processing on stale sessions — without this, a race between `close_session` and `update_summary` (which re-fetches the session from DB) can leave `_active_session` pointing to a closed session, causing infinite post-processing.

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

- All persistent subsystems (sessions, tasks) share a single `Database` instance with one `AsyncEngine` and `async_sessionmaker` backed by `tachikoma.db`
- `expire_on_commit=False` is used on the shared `async_sessionmaker` to allow attribute access on `SessionRecord` instances after commit (before `to_domain()` conversion)
- SQLite stores datetimes as naive ISO strings; a `_ensure_utc()` helper restores UTC tzinfo on read so callers always receive timezone-aware datetimes
- The `BootstrapContext.extras` field is used to pass the `Database`, repository, and registry between hooks and to `__main__.py` — see [workspace-bootstrap design](workspace-bootstrap.md)
