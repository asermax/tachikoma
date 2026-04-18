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
| R1 | Each session tracks: unique ID, SDK session ID, transcript path, summary, last exchange, start timestamp, end timestamp, post-processing timestamp, error flag |
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
| R15 | Persist context entries injected into a session as queryable records tied to the session — one entry per context source (each foundational context file as a separate entry, each pre-processing provider as one entry, each session transition artifact as one entry) |
| R16 | Context entries are queryable by session ID |
| R17 | Context persistence failures must not interrupt active conversations (graceful degradation, consistent with session tracking pattern) |
| R18 | Before reopening a session, validate that the SDK transcript file exists on the local filesystem — reject sessions created on other machines |
| R19 | Before reopening a session, validate that the session's `started_at` is within the configured max age — reject stale sessions |
| R20 | `get_recent_closed()` filters returned sessions at two levels: repository-level (non-null `sdk_session_id`, non-null `summary`, within time window) and registry-level (valid transcript file on local filesystem, `started_at` within configured max age) |
| R21 | When the SDK returns a UTF-8 encoding error (e.g. surrogates in its internal transcript), mark the session as errored — this excludes it from resumable candidates and makes the error recoverable |
| R22 | Context entries support an optional metadata field (JSON dict) for structured data that varies by entry type — existing entries without metadata continue to work normally |
| R23 | Each session tracks the last assistant response (`last_exchange`), updated by the per-message pipeline after each exchange; nullable — null until first exchange processed; filters to text after the last tool call when tools are used; falls back to full response when no text follows the last tool call |

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
- The `transcript_path` is derived from the SDK session ID using the known SDK transcript directory structure (preserving the leading `-` from the path sanitization)
- The `transcript_path` always points at the SDK-owned location (`~/.claude/projects/...`); the workspace-archived copy produced at session close lives at `memories/transcripts/<sdk-session-id>.jsonl` and is derivable from `sdk_session_id` without a dedicated field — see [memory-extraction](../memory/memory-extraction.md) for the archival behavior

### Session Closing (R3)

Sessions close when a boundary detection topic shift is detected or on clean shutdown. Idle timeout triggers post-processing without closing the session (see Idle Post-Processing below).

**Acceptance Criteria**:
- Given an active session, when a conversation end signal is received, then the session's `ended_at` is set to the current timestamp
- Given a session is already closed, when a close signal is received again, then the active session reference is cleared and the operation completes without error or database change
- Given no active session exists, when a close signal is received, then the operation is a no-op

### Idle Post-Processing (R1)

After a configurable idle timeout, the post-processing pipeline runs on the open session without closing it. The session stays open so the next message goes through boundary detection, which either continues the session or routes to a new/resumed one.

**Acceptance Criteria**:
- Given an active session with no message exchange for longer than the configured idle timeout, when the coordinator is not busy, then post-processing runs but the session remains open (`ended_at` stays None)
- Given idle post-processing already completed (`processed_at >= last_message_time`), when the idle loop checks again, then it skips
- Given idle post-processing is running, when the idle loop or shutdown checks, then it skips
- Given idle post-processing completed with no new messages, when shutdown occurs, then the session is closed but post-processing is skipped
- Given a new message arrives after idle post-processing, when idle timeout fires again, then post-processing fires again (`processed_at < last_message_time`)
- Given idle post-processing fired, when a new message arrives, then boundary detection runs (session has summary) and the session continues or transitions normally

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

### Last Exchange Update (R23)

After each agent response, the per-message pipeline persists the agent's response text as the session's `last_exchange`. When the response contains tool calls interspersed with text, only the text segment following the final tool call is stored — intermediate planning text (e.g., "Let me check...", "Now let me fix...") is filtered out to improve boundary detection signal quality.

**Acceptance Criteria**:
- Given an active session, when the per-message pipeline runs after an agent response with tool calls, then only the text after the last tool call is stored to the session's `last_exchange` field
- Given an active session, when the per-message pipeline runs after an agent response with no tool calls, then the full response text is stored to `last_exchange` (no filtering applied)
- Given an active session, when the agent response ends with a tool call and no trailing text, then the full response text is stored to `last_exchange` (noisy signal preferred over no signal for boundary detection)
- Given an active session, when the agent response is empty or whitespace-only, then `last_exchange` is not updated — the previous value is retained (or null if no prior response)
- Given a session is closed and later reopened, when it becomes active again, then `last_exchange` retains its previous value from before the session was closed
- Given the per-message processor fails to persist `last_exchange`, when the error occurs, then it is logged and the conversation continues (consistent with per-message pipeline error isolation)

### Session Reopening (R11, R14, R18, R19)

The registry can reopen a closed session, making it the active session again for resumption. Validates transcript availability and session age before allowing resumption.

**Acceptance Criteria**:
- Given a closed session with a valid ID, transcript that exists locally, and `started_at` within the max age, when `reopen_session()` is called, then its `ended_at` is cleared, `last_resumed_at` is set to the current timestamp, and it becomes the active session
- Given a session ID that does not exist, when `reopen_session()` is called, then it returns None and logs a warning
- Given a session with no `transcript_path`, when `reopen_session()` is called, then it returns None and logs a warning
- Given a session whose transcript file does not exist on the local filesystem, when `reopen_session()` is called, then it returns None and logs a warning
- Given a session whose `started_at` is older than the configured max age, when `reopen_session()` is called, then it returns None and logs a warning
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
- Given errored sessions (error flag set) within the window, when queried, then they are excluded
- Given sessions that pass repository-level filters but have missing transcript files, when `get_recent_closed()` is called, then those sessions are excluded at the registry level
- Given sessions that pass all other filters but whose `started_at` exceeds the configured max session age, when `get_recent_closed()` is called, then those sessions are excluded at the registry level
- Given sessions passing all filters (non-null `sdk_session_id`, non-null `summary`, within time window, transcript exists, within max age), when `get_recent_closed()` is called, then those sessions are included in results

### Context Entry Persistence (R15, R16, R17)

Each session can have associated context entries that capture what context was injected into the agent's system prompt during that session. Entries are persisted at lifecycle points and loaded for system prompt assembly.

**Acceptance Criteria**:
- Given a session context entry, then it has: session ID (FK), owner identifier (string), and content (text)
- Given a session with context entries, when queried by session ID, then all entries for that session are returned in the order they were persisted
- Given a session ID with no context entries, then an empty list is returned
- Given a context entry fails to save, then the error is logged and the conversation continues
- Given a new session created after a topic shift, then entries may include owners such as: soul, user, agents, previous-summary, memories, projects, skills
- Given a resumed session with bridging context, then a bridging-context entry is added alongside the session's original entries

### Context Entry Metadata (R22)

Context entries can carry optional structured metadata for provider-specific data. The metadata field is a nullable JSON dictionary — different entry types use it for different purposes.

**Acceptance Criteria**:
- Given a context entry with metadata, when it is persisted, then the metadata dict is serialized and stored
- Given a context entry with metadata, when it is loaded, then the metadata dict is deserialized and available on the entry
- Given a context entry without metadata, when it is loaded, then the metadata field is None
- Given existing context entries without metadata, when the schema is migrated, then those entries remain valid with metadata=None

### Error Marking (R21)

When the SDK encounters a UTF-8 encoding error in its internal conversation transcript (e.g. lone surrogate characters), the coordinator marks the session as errored to prevent resuming a contaminated session.

**Acceptance Criteria**:
- Given the SDK returns an encoding error (e.g. "surrogates not allowed"), when the coordinator detects it, then the session's `error` flag is set to True and the in-memory SDK session ID is cleared
- Given a session with `error=True`, when `get_recent_closed()` queries candidates, then it is excluded from resumable candidates
- Given a session with `error=True`, when its status is queried, then it reports "interrupted" regardless of other fields

### Graceful Degradation (R9)

Session tracking failures never crash the conversation.

**Acceptance Criteria**:
- Given a conversation is active, when a session registry operation fails, then the error is logged and the conversation continues uninterrupted
