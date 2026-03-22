# Session Tracking

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A persistent registry of conversation sessions that tracks when conversations start, end, and what transcript files they produced. The session registry provides the lifecycle foundation that boundary detectors use to signal session transitions and that post-processing pipelines use to find completed conversations for analysis. On startup, crash recovery detects sessions left open from ungraceful shutdowns and closes them with best-effort timestamps.

## User Stories

- As a post-processing pipeline, I need to query completed sessions so that I can find conversations to analyze
- As the system, I need a persistent record of session lifecycles so that conversation boundaries and metadata survive restarts
- As a user, I want the system to reopen a previous session when I return to a topic so that my earlier conversation context is restored

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Maintain a persistent registry of conversation sessions with lifecycle tracking |
| R1 | Each session tracks: unique ID, SDK session ID, transcript path, summary, start timestamp, end timestamp |
| R2 | Create a new session when a conversation starts (first message or boundary detection) |
| R3 | Close a session when a conversation ends (set end timestamp and final metadata) |
| R4 | Query sessions by time range |
| R5 | Query sessions by session ID |
| R6 | Store the registry as a persistent file in the workspace data folder, supporting structured queries |
| R7 | Registry data survives application restarts |
| R8 | On startup, detect sessions left open from ungraceful shutdowns and mark them as interrupted |
| R9 | Session tracking failures must not interrupt active conversations |
| R10 | Rolling conversation summary is updated on the session record by per-message post-processing |
| R11 | Reopen a closed session by clearing its `ended_at` and setting `last_resumed_at`, making it the active session again |
| R12 | Track each resumption with a dedicated SessionResumption record capturing session ID, resumption timestamp, and previous close timestamp |
| R13 | Query recently closed sessions within a configurable time window for resumption candidate matching |
| R14 | Track `last_resumed_at` timestamp on sessions for downstream processor awareness |

## Behaviors

### Session Creation (R1, R2)

The system creates a new session when the coordinator receives the first message in a new conversation (or when a boundary detector signals a new conversation).

**Acceptance Criteria**:
- Given the agent receives the first message in a new conversation, when no active session exists, then a new session is created with a unique ID and the current timestamp as `started_at`
- Given a boundary detector signals a new conversation, when the current session is closed, then a new session is created for the incoming message
- Given a session is created, then its `ended_at` is null, `sdk_session_id` is null, `transcript_path` is null, and `summary` is null until the per-message pipeline updates it after the first agent response
- Given a session creation is already in progress, when another creation signal arrives, then only one session is created (the operation is serialized)

### Session Metadata Update (R1)

When the coordinator receives a Result event from the SDK, it populates the session's SDK metadata.

**Acceptance Criteria**:
- Given an active session, when the coordinator produces a Result event with `session_id`, then the session's `sdk_session_id` and `transcript_path` are populated from the SDK data
- The `transcript_path` is derived from the SDK session ID using the known SDK transcript directory structure

### Session Closing (R3)

Sessions close when a boundary detection topic shift is detected or on clean shutdown.

**Acceptance Criteria**:
- Given an active session, when a conversation end signal is received, then the session's `ended_at` is set to the current timestamp
- Given a session is already closed, when a close signal is received again, then the operation is idempotent (no error, no change)
- Given no active session exists, when a close signal is received, then the operation is a no-op

### Querying (R4, R5)

The registry supports querying sessions by time range and by ID.

**Acceptance Criteria**:
- Given sessions exist, when querying by time range, then all sessions whose time span overlaps the query range are returned ordered by `started_at` descending (open sessions are treated as ongoing)
- Given a session ID, when querying by ID, then the matching session is returned or None if not found
- Given no sessions match a query, then an empty result is returned

### Persistence (R6, R7)

The session registry is stored in the shared `.tachikoma/tachikoma.db` database and auto-creates its storage structure on first access.

**Acceptance Criteria**:
- Given the registry is stored in the workspace data folder, when the application restarts, then all previously recorded sessions are still queryable
- Given the storage does not exist, when the registry is first accessed, then it is created with the correct structure

### Crash Recovery (R8)

On startup, the recovery hook detects and closes sessions left open from ungraceful shutdowns.

**Acceptance Criteria**:
- Given the application starts with sessions that have null `ended_at`, when the recovery hook runs, then those sessions have their `ended_at` set to a best-effort timestamp (transcript file mtime if available, otherwise current time)
- Given a session has `ended_at` set but `sdk_session_id` is null, then it is identified as "interrupted"
- Given the recovery hook runs and no sessions have null `ended_at`, then the hook completes with no side effects

### Summary Update (R1, R10)

When the per-message pipeline completes, it updates the session's rolling conversation summary.

**Acceptance Criteria**:
- Given an active session, when the per-message pipeline produces a new summary, then the session's `summary` field is updated and persisted
- Given the session is a frozen dataclass, when the summary is updated, then the active session reference is refreshed with the updated value

### Session Reopening (R11, R14)

The registry can reopen a closed session, making it the active session again for resumption.

**Acceptance Criteria**:
- Given a closed session with a valid ID, when `reopen_session()` is called, then its `ended_at` is cleared, `last_resumed_at` is set to the current timestamp, and it becomes the active session
- Given a session ID that does not exist, when `reopen_session()` is called, then it returns None and logs a warning
- Given a session that is already open, when `reopen_session()` is called, then it returns None and logs a warning
- Given a session that is already the active session, when `reopen_session()` is called, then it returns None and logs a warning

### Resumption Tracking (R12)

Each resumption event is recorded as a dedicated `SessionResumption` record for audit and history.

**Acceptance Criteria**:
- Given a session is successfully resumed, then a `SessionResumption` record is created capturing the session ID, resumption timestamp, and previous close timestamp
- Given a session has been resumed multiple times, when its resumption history is queried, then all resumption records are available in chronological order
- Given resumption tracking fails (database error), then the session is still resumed successfully — tracking is best-effort

### Recent Sessions Query (R13)

The registry provides a query for recently closed sessions within a configurable time window.

**Acceptance Criteria**:
- Given a time window and reference timestamp, when `get_recent_closed()` is called, then only sessions closed within that window with non-null SDK session IDs and non-null summaries are returned
- Given sessions closed outside the time window, when queried, then they are excluded
- Given only interrupted sessions (no `sdk_session_id`) exist within the window, when queried, then they are excluded

### Graceful Degradation (R9)

Session tracking failures never crash the conversation.

**Acceptance Criteria**:
- Given a conversation is active, when a session registry operation fails, then the error is logged and the conversation continues uninterrupted
