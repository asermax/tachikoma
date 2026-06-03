# Design: Task Management

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/tasks/task-management.md](../../feature-specs/tasks/task-management.md)
**Status**: Current

## Purpose

This document explains the design rationale for task management: the data model, persistence layer, MCP tools for agent interaction, and the instance generation mechanism.

## Problem Context

Tachikoma needs persistent task definitions that the agent can create and manage during conversations, with automatic instance generation when schedules fire. The data model must support both cron-based recurring schedules and one-shot datetime schedules, with clear separation between definitions (what to do) and instances (individual executions).

**Constraints:**
- SQLAlchemy async + aiosqlite is the established persistence pattern (ADR-007)
- Bootstrap hooks (DES-003) are the initialization mechanism
- MCP tools follow the existing SDK MCP Tool Server Factory pattern (DES-006)
- Recurring time-based work goes through the central scheduler (DES-010) as Jobs with interval or cron triggers
- Task data must be independent of the sessions subsystem

**Interactions:**
- Session task scheduler (`session-task-execution`): queries pending session instances
- Background task runner (`background-task-execution`): queries pending background instances, updates status
- Coordinator (`core-architecture`): receives task MCP tools via `mcp_servers` parameter
- Bootstrap (`__main__.py`): `tasks_hook` initializes the repository and runs crash recovery

## Design Overview

The task management subsystem lives in `src/tachikoma/tasks/` as a self-contained package. It follows the same persistence patterns as the sessions subsystem: frozen dataclasses for domain types, ORM models internal to the repository, and a repository class providing async CRUD operations. All tables live in the shared `tachikoma.db` database alongside session tables.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/tasks/__init__.py` | Public API re-exports | Clean package interface |
| `src/tachikoma/tasks/model.py` | `TaskDefinition` and `TaskInstance` frozen dataclasses (domain types); `TaskDefinitionRecord` and `TaskInstanceRecord` ORM models; `TaskStatus` (`"pending" \| "running" \| "waiting" \| "completed" \| "failed"`) and `TaskType` constant maps; `ScheduleConfig` type. `TaskDefinition` carries `since` (auto-stamped, anchors stale-cron prevention). `TaskInstance` carries pause/resume state: `sdk_session_id` (resume target captured from the latest `ResultMessage`), `user_response` (pending reply from main agent), `updated_at` (auto-stamped, anchors the wait_timeout sweep) | Domain types frozen; ORM models internal to persistence; schedule stored as JSON column; `from_json` recovers legacy bare ISO datetime strings as one-shot schedules; all parse failures raise `ValueError` (never bare `JSONDecodeError` or `KeyError`); `TaskDefinitionRecord.since` uses `default=lambda: datetime.now(UTC)` + `onupdate=lambda: datetime.now(UTC)` so every INSERT and UPDATE refreshes the stale-cron anchor; `TaskInstanceRecord.updated_at` uses the same pattern (DES-009) so every repository write refreshes the value without caller involvement |
| `src/tachikoma/tasks/repository.py` | `TaskRepository` — async SQLAlchemy CRUD for definitions and instances; `list_enabled_definitions()` and `list_disabled_definitions()` for filtered queries; `_to_domains_with_isolation()` for per-record error isolation with auto-disable; crash recovery (mark running as failed; leaves waiting untouched); `get_ready_background_instances()` returns the pending ∪ waiting-with-response union for the runner; `list_expired_waiting_instances(timeout_seconds)` feeds the wait_timeout sweep; `update_instance(id, **fields)` is the single field-agnostic write path — callers pass arbitrary column updates and `updated_at` auto-stamps via the ORM (DES-009) | Receives shared `async_sessionmaker` from `Database`; follows ADR-007 pattern; list methods auto-disable corrupted definitions instead of failing the entire query; ready-instance query is a single `select` with `OR` so fresh and resumable tasks flow through one code path; `list_expired_waiting_instances` filters out rows with `updated_at IS NULL` (legacy pre-DES-009 rows that have not been written since the column was added) |
| `src/tachikoma/tasks/tools.py` | `create_task_tools_server(repository, timezone)` — MCP server factory receiving `ZoneInfo` at construction; `_parse_schedule(schedule, tz)` stamps naive datetimes with configured timezone, preserves aware as-is; `_format_schedule(schedule, tz)` converts display to configured timezone; `list_tasks` (defaults to enabled-only, `archived` parameter for disabled; output includes task ID for referencing in other tools, prompts excluded for compact output; `last_fired_at` converted to configured timezone), `get_task` (returns full details including complete prompt for a single task by ID; `last_fired_at` and `created_at` converted to configured timezone), `create_task`, `update_task` (supports `task_type` changes via `Literal` validation; resets `last_fired_at` on schedule change to enable one-shot re-scheduling; validates one-shot schedules are in the future consistent with `create_task`), `delete_task`, `run_task_now` (immediate background task execution — two modes: by-reference via `task_id` snapshots the definition's prompt without mutating it; ad-hoc via `prompt` creates a transient instance with `definition_id=None`; `RunTaskNowArgs` uses a `model_validator` to enforce exactly one of `task_id` or `prompt`, with optional `name` only valid with `prompt`; background-only for by-ref mode; no cross-instance concurrency gate), and `respond_to_task` (routes a user's reply back to a `waiting` background task — enforces `instance.status == "waiting"` and `user_response is None` before persisting, returning an error otherwise); Pydantic `BaseModel` classes (`ListTasksArgs`, `GetTaskArgs`, `CreateTaskArgs`, `UpdateTaskArgs`, `DeleteTaskArgs`, `RunTaskNowArgs`, `RespondToTaskArgs`) for arg validation and type coercion; enriched `@tool()` descriptions with parameter documentation including timezone-aware schedule formats | Factory receives `ZoneInfo`, passes to `_parse_schedule` and `_format_schedule` via closures; uses `replace(tzinfo=tz)` for naive, `astimezone(tz)` for display; all displayed timestamps (schedules, `last_fired_at`, `created_at`) converted to configured timezone; list/detail pattern: `list_tasks` is compact (no prompt), `get_task` returns full details; `update_task` resets `last_fired_at` when schedule changes (the old fire time is meaningless for a new schedule; for one-shot tasks, the instance generator requires `last_fired_at=None` to fire; for cron tasks, the anchor logic handles `None` by falling back to start-of-hour); `UpdateTaskArgs.task_type` uses `Literal["session", "background"]` for automatic validation; `respond_to_task` performs the status + already-responded check before writing (dual-gate authority — the tool enforces the invariant, the respondable-notification prompt is only a guidance hint); `run_task_now` is registered unconditionally (both with and without `respond_to_task`); `TaskRepositoryError`-specific error handling surfaces root causes via `__cause__`; follows DES-006 |
| `src/tachikoma/tasks/hooks.py` | `tasks_hook` — bootstrap hook (DES-003): retrieves shared `Database` from extras, creates repository, runs crash recovery; stores `task_repository` in `bootstrap.extras` | Subsystem-owned hook; runs after `database_hook` |
| `src/tachikoma/tasks/scheduler.py` | Tick entry points driven by the central scheduler (DES-010): `instance_generator_tick()` (strict cron firing, stale-cron prevention via `since`, period-aware dedup), `session_task_scheduler_tick()` (enqueues pending session instances into the buffer), `one_shot_cleanup_tick()` (thin wrapper around `repository.cleanup_expired_one_shot_definitions`). Plus `_create_pending_instance()` helper and `get_timezone(settings)` shared utility | Each tick is a single pass — no `while` loop, no sleep, no top-level try/except (owned by the central scheduler per DES-010); `get_timezone` has no fallback logic — validation happens at config load; stale-cron check advances CronSim anchor past `since` to find the next valid occurrence |
| `src/tachikoma/tasks/executor.py` | `BackgroundTaskRunner` (stateful-tick class per DES-010: holds the semaphore and in-flight executor dict across ticks; `tick()` queries ready instances and spawns `BackgroundTaskExecutor` tasks under the semaphore; `shutdown()` drains in-flight executor tasks on application shutdown); `expired_waiter_sweep()` (standalone Job that fails waiting instances past `wait_timeout`); `BackgroundTaskExecutor` | Stateful tick uses a class because the semaphore and executor-task dict must persist across ticks; `shutdown()` is called from `__main__.py`'s finally block *after* the scheduler task is cancelled so in-flight executors get a chance to cancel cleanly; waiter sweep extracted from the runner into its own Job so its cadence is independent of the runner tick |
| `src/tachikoma/tasks/repository.py` (cleanup extension) | `cleanup_expired_one_shot_definitions(retention_hours)` — deletes fired one-shot definitions (`schedule.type == "once"` AND `last_fired_at IS NOT NULL`) whose associated instances are all terminal and whose retention anchor (`max(instance.completed_at)`, falling back to `last_fired_at` for zero-instance defs) is older than the threshold; deletes instances first, then definition | Single repository method — the Job wrapper stays thin; JSON substring match on `'"type": "once"'` relies on `to_json` serialization with a space after the colon; retention anchor composes latest instance completion with `last_fired_at` fallback so zero-instance-but-fired definitions are also eligible |
| `src/tachikoma/database.py` | Shared `Database` class with `Base(DeclarativeBase)`, `AsyncEngine`, `async_sessionmaker`; `database_hook` bootstrap hook | All ORM models share one `Base`; single engine for all subsystems |
| `src/tachikoma/context/loading.py` (`SYSTEM_PREAMBLE_TEMPLATE`) | Timezone-aware tasks documentation in the system prompt preamble: task types, scheduling formats with timezone behavior, Date and Time section, MCP tool descriptions with parameter documentation, cross-references, and `send_notification` tool description for background tasks | `SYSTEM_PREAMBLE_TEMPLATE` with `{timezone}` placeholder; `render_system_preamble(timezone)` resolves and formats; follows ADR-008 append pattern |

### Cross-Layer Contracts

**Task creation during conversation:**

```mermaid
sequenceDiagram
    actor User
    participant Channel
    participant Coord as Coordinator
    participant SDK as ClaudeSDKClient
    participant MCP as Task MCP Tools
    participant Repo as TaskRepository

    User->>Channel: "remind me to check emails every morning at 9"
    Channel->>Coord: send_message(text)
    Coord->>SDK: query(text) with mcp_servers=[task-tools]
    SDK->>MCP: create_task({name, schedule, type, prompt})
    MCP->>MCP: _parse_schedule(schedule, tz) → ScheduleConfig
    MCP->>Repo: create definition
    Repo-->>MCP: definition created
    MCP-->>SDK: "Task created successfully"
    SDK-->>Coord: agent response
    Coord-->>Channel: AgentEvent stream
    Channel-->>User: "I've set up a daily reminder..."
```

**Error contract:**
- MCP tool errors: return `{"is_error": true, "content": [...]}` — agent sees error message and can retry; `TaskRepositoryError` is caught specifically to surface root cause via `__cause__`; unexpected errors use a generic fallback
- Instance generator errors: logged, loop continues on next tick
- Repository errors: wrapped in `TaskRepositoryError`, logged at call sites

## Modeling

### TaskDefinition

```
TaskDefinition (frozen dataclass)
├── id: str                          (UUID)
├── name: str                        (human-readable label)
├── schedule: ScheduleConfig         (cron expression or one-shot datetime)
├── task_type: str                   ("session" or "background")
├── prompt: str                      (instruction for the agent)
├── enabled: bool                    (default True)
├── last_fired_at: datetime | None   (last time an instance was generated)
├── since: datetime                  (auto-stamped, anchors stale-cron prevention)
└── created_at: datetime             (creation timestamp)
```

### TaskInstance

```
TaskInstance (frozen dataclass)
├── id: str                          (UUID)
├── definition_id: str | None        (FK → task_definitions.id, null for transient)
├── task_type: str                   ("session" or "background", copied from definition)
├── status: str                      ("pending", "running", "waiting", "completed", "failed")
├── prompt: str                      (copied from definition at creation time)
├── scheduled_for: datetime          (cron match time for cron tasks; schedule.at for one-shot tasks)
├── started_at: datetime | None      (when execution began)
├── completed_at: datetime | None    (when execution finished)
├── result: str | None               (completion/failure summary)
├── sdk_session_id: str | None       (SDK session to resume when pausing; set on await_response)
├── user_response: str | None        (pending reply from main agent; consumed atomically on resume)
├── workflow_id: str | None          (soft reference → workflow_states.id; null for regular tasks;
│                                     non-null = workflow step task; excluded from list_tasks)
├── updated_at: datetime | None      (auto-stamped via DES-009; anchors the wait_timeout sweep)
└── created_at: datetime             (creation timestamp)
```

### ScheduleConfig

```
ScheduleConfig (frozen dataclass)
├── type: str                        ("cron" or "once")
├── expression: str | None           (cron expression, only when type="cron")
└── at: datetime | None              (target datetime, only when type="once")
```

### Entity relationships

```mermaid
erDiagram
    TaskDefinition ||--o{ TaskInstance : "generates"
    TaskDefinition {
        string id PK
        string name
        json schedule
        string task_type
        string prompt
        boolean enabled
        datetime last_fired_at
        datetime since
        datetime created_at
    }
    TaskInstance {
        string id PK
        string definition_id FK
        string task_type
        string status
        string prompt
        datetime scheduled_for
        datetime started_at
        datetime completed_at
        string result
        string sdk_session_id
        string user_response
        string workflow_id
        datetime updated_at
        datetime created_at
    }
```

Note: `TaskInstance.definition_id` is nullable — transient instances (notifications from background task results) have no parent definition. `TaskInstance.workflow_id` is nullable — when set, the instance is a workflow step task (see [background-task-execution](background-task-execution.md)) and excluded from `list_tasks`. The reference is soft (no FK constraint) because workflow state may be soft-deleted.

### Task status lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: instance created
    pending --> running: execution starts
    running --> completed: evaluator marks done
    running --> failed: stuck/error/max iterations
    running --> waiting: agent calls send_notification with await_response=true
    waiting --> running: user_response set + runner tick picks up
    waiting --> failed: wait_timeout expired
    completed --> [*]
    failed --> [*]
```

Background tasks can cycle through `waiting` multiple times (each `await_response` pause stores the latest `sdk_session_id`). The `waiting` state is only reachable for background tasks — session tasks run inside the main conversation and don't use the evaluator loop.

## Data Flow

### Instance generation flow

```
1. Instance generator loop wakes up (~60s interval)
2. Query all enabled definitions from repository
3. For each definition:
   a. If cron schedule:
      - Compute anchor in evaluation timezone (convert last_fired_at from UTC to tz, or start-of-hour for first run)
      - Get next fire time from CronSim using anchor
      - Stale-cron prevention: if next_fire <= since (converted to tz), advance CronSim anchor past since to find the next valid occurrence
      - If next fire time > now (hasn't passed), skip
      - Compute cron_match_utc from next fire time
      - Period-aware duplicate check: query for pending/running/completed instance with matching scheduled_for (failed excluded for retry)
      - If duplicate found, log suppression and skip
      - Create TaskInstance(status="pending", scheduled_for=cron_match_utc)
      - Update definition.last_fired_at = now (ensures CronSim anchors past missed periods on catch-up)
   b. If one-shot schedule (hasn't fired yet and time has passed):
      - Check no pending/running instance exists for this definition
      - Create TaskInstance(status="pending", scheduled_for=schedule.at)
      - Update definition.last_fired_at, set definition.enabled=false
4. Sleep until next tick
```

### One-shot cleanup flow

```
1. Central scheduler (DES-010) fires the one_shot_cleanup Job on its cron trigger (0 3 * * *)
2. Job calls repository.cleanup_expired_one_shot_definitions(retention_hours)
3. Repository selects candidate definitions: schedule contains '"type": "once"', last_fired_at IS NOT NULL,
   no instance with status NOT IN ('completed', 'failed') (NOT EXISTS subquery)
4. For each candidate:
   a. Query max(completed_at) over that definition's instances
   b. Anchor = max completed_at if any instance exists, else definition.last_fired_at
   c. If anchor >= threshold (now - retention_hours), skip
   d. Delete all instances for that definition
   e. Delete the definition
5. Commit. Return deleted count. Job logs the count.
```

### Task creation flow

```
1. Coordinator builds ClaudeAgentOptions with mcp_servers={"task-tools": server}
2. Agent receives user request like "remind me to check emails at 9am"
3. Agent calls create_task tool with name, schedule, type, prompt
4. Tool validates:
   a. Required fields present (name, schedule, type, prompt)
   b. Type is "session" or "background"
   c. Schedule parsed via _parse_schedule(schedule, tz):
      - datetime.fromisoformat(schedule): if naive, stamp with configured tz; if aware, preserve
      - Falls back to CronSim for cron expressions
   d. One-shot datetime must be in the future (tz-aware comparison)
5. Tool calls repository.create_definition()
6. Returns success/error message to agent
7. Agent confirms to user
```

### Task listing flow

```
1. Agent calls list_tasks (optionally with archived=true)
2. Tool checks archived parameter (default: false)
3. If archived: calls repository.list_disabled_definitions()
   If not archived: calls repository.list_enabled_definitions()
4. Formats one-shot schedules via _format_schedule(schedule, tz): converts to configured timezone via astimezone(tz)
5. Formats last_fired_at via astimezone(tz): converts from UTC to configured timezone for display
6. Returns compact formatted list with task ID, name, type, schedule, and status per entry (prompts excluded)
   Or "No active/archived tasks found." if empty
```

### Task detail flow

```
1. Agent calls get_task with a task_id (obtained from list_tasks)
2. Tool calls repository.get_definition(task_id)
3. If not found: returns error "Task '<id>' not found."
4. Formats full details: name, ID, type, status, schedule, timestamps (last_fired_at and created_at converted to configured timezone via astimezone(tz)), and complete prompt
5. Returns formatted detail view
```

### On-demand execution flow

```
1. Agent calls run_task_now with either task_id or prompt (+ optional name)
2. Pydantic model_validator enforces: exactly one of task_id or prompt;
   name only valid with prompt
3. If task_id provided (by-reference mode):
   a. repository.get_definition(task_id) → if None, return "not found"
   b. If definition.task_type != "background", return error
   c. Build TaskInstance with definition_id=task_id, prompt=definition.prompt
4. If prompt provided (ad-hoc mode):
   a. Build transient TaskInstance with definition_id=None, prompt=args.prompt
5. Common: repository.create_instance(instance) with error handling
6. Log info line with instance ID, mode (by_ref/ad_hoc), source identifier
7. Return success content with instance ID
8. Background task runner's next tick picks up the pending instance via
   get_ready_background_instances() (filters on status only, not scheduled_for)
```

## Key Decisions

### Shared database file

**Choice**: Store task definitions and instances in the shared `tachikoma.db` alongside session tables.
**Why**: All persistent subsystems share a single `Database` class with one `AsyncEngine` and `async_sessionmaker`. This simplifies engine lifecycle (one create, one dispose), reduces resource usage, and establishes a cleaner foundation as more persistent features are added.

**Consequences**:
- Pro: Single engine lifecycle — simpler shutdown, fewer resources
- Pro: All subsystems use the same `Base(DeclarativeBase)` and `session_factory`
- Pro: Future persistent features follow the same pattern naturally
- Con: Cannot reset task data independently of session data

### MCP tools on coordinator

**Choice**: Register the task tools MCP server on the coordinator's `ClaudeAgentOptions.mcp_servers`, making them available in every conversation turn.
**Why**: The agent needs to create/manage tasks during live conversations. The MCP tool pattern (DES-006) creates `McpSdkServerConfig` instances via factory functions — the same approach works for coordinator-level registration.

**Consequences**:
- Pro: Agent can manage tasks naturally during conversation
- Pro: Follows established MCP tool pattern
- Con: Tools are available in every turn (minor overhead)

### Task guidance in system preamble

**Choice**: Include task types, scheduling formats, and tool descriptions (including `send_notification` for background tasks) in `SYSTEM_PREAMBLE` as a static Tasks section.
**Why**: The agent needs task domain knowledge to interpret user requests (e.g., choosing session vs background type) before invoking MCP tools. Tool schemas describe parameters but not when to use them.

**Consequences**:
- Pro: Agent has task context regardless of whether tasks exist
- Pro: Follows ADR-008 append pattern, consistent with Skills preamble section
- Con: Preamble content must be kept in sync with tool behavior

### Schema creation via create_all with pragma-based upgrades

**Choice**: The shared `Database.initialize()` uses `Base.metadata.create_all()` for table creation, with pragma-based column checks for upgrading existing databases.
**Why**: Starting fresh with `create_all` is the simplest path. Pragma-based checks handle incremental schema evolution (e.g., adding columns) without requiring a full migration framework.

**Consequences**:
- Pro: Simplest initial setup
- Pro: Handles both fresh and existing databases
- Con: Manual pragma checks for each new column addition

### Timezone-aware schedule parsing

**Choice**: Stamp naive datetimes with the configured timezone via `replace(tzinfo=tz)` rather than `astimezone(tz)`.
**Why**: `replace` means "this datetime is expressed in timezone X" — preserves wall-clock values. `astimezone` means "convert this instant to timezone X" — would adjust clock values, which is wrong for user-intended wall-clock times.

**Consequences**:
- Pro: "3pm" means 3pm in the configured timezone
- Pro: Explicit tz offsets and `Z` suffix preserved as-is
- Pro: No dependency on system local time during parsing

### Timezone plumbing via factory closure

**Choice**: Resolve timezone once in `__main__.py` as `ZoneInfo(settings.tasks.timezone)` and inject via `create_task_tools_server(repository, timezone)`. Inner tool closures capture the timezone from the factory.
**Why**: Follows DES-006 factory pattern. Single resolution point; no repeated lookups.

**Consequences**:
- Pro: Clean single-resolution pattern
- Pro: Consistent with existing factory parameter passing

### Strict cron firing condition

**Choice**: Fire only when the cron match time has already passed (`next_fire <= now_tz`), with no tolerance window.
**Why**: A tolerance window caused early and repeated firing within the same cron period. Accepting up to 60s lateness (bounded by the generator interval) is preferable to duplicate instances.

### Period-aware duplicate check via `scheduled_for`

**Choice**: Store the cron match time in `scheduled_for` and check for existing instances matching that time and a non-failed status.
**Why**: Using the cron match time as a period identifier enables deduplication across instance status changes (e.g., a completed instance still blocks a duplicate). The previous approach only checked pending/running, allowing duplicates after completion within the same period.

### Schedule update resets `last_fired_at`

**Choice**: When `update_task` changes the schedule, reset `last_fired_at` to `None`. When only `enabled` changes (no new schedule), leave `last_fired_at` untouched.
**Why**: The instance generator checks `definition.last_fired_at is None` as a guard for one-shot task firing. Without resetting, a re-enabled one-shot with a new schedule would never fire because `last_fired_at` was still set from the previous execution. The reset applies to all schedule types — for cron tasks, the anchor logic handles `None` by falling back to start-of-hour. Leaving `last_fired_at` untouched when only `enabled` changes prevents a stale one-shot schedule from re-firing.

**Consequences**:
- Pro: Re-scheduling disabled one-shot tasks works correctly
- Pro: Cron schedule changes get a fresh anchor point
- Pro: Re-enabling without schedule change is safe (stale schedules don't fire)

### Auto-stamped `updated_at` for pause/resume timeout anchoring

**Choice**: `TaskInstanceRecord.updated_at` is declared with SQLAlchemy Python-side `default=lambda: datetime.now(UTC)` and `onupdate=lambda: datetime.now(UTC)` (DES-009). No repository caller passes `updated_at` — the ORM stamps it on every INSERT and UPDATE.
**Why**: The `wait_timeout` sweep needs a reliable "last activity" anchor per waiting instance. If callers had to stamp `updated_at` manually on the waiting-transition write (and on the consume-on-pickup write, and on any future write path), a missed stamp would make the sweep either too aggressive (missing updates → appears stale) or silently incorrect. Pushing the invariant to the ORM layer means new write paths (crash recovery, future admin tools, batch updates) inherit the behavior automatically. Python-side lambdas (not `func.now()`) preserve tz-aware datetimes on SQLite, which the rest of the codebase requires.

**Consequences**:
- Pro: Every write path refreshes the anchor — impossible to forget
- Pro: `list_expired_waiting_instances` can trust `updated_at` as the sole timeout driver
- Pro: Same pattern is now available to migrate other manually-stamped timestamps (see DES-009 Scope)
- Con: `updated_at IS NULL` rows exist in older databases created before the column was added — `list_expired_waiting_instances` explicitly excludes them so legacy data doesn't get aggressively expired

### respond_to_task tool — dual-gate via status check

**Choice**: Register `respond_to_task` on the same task-tools MCP server as the CRUD tools, but only when the server is built for the main conversation — the `create_task_tools_server` factory accepts an `include_respond_tool` flag, and `__main__.py` passes `False` when building the server injected into `background_task_runner` via `extra_mcp_servers`. The handler enforces two preconditions before writing: `instance.status == "waiting"` and `instance.user_response is None`. Only then does it persist the response via `update_instance(task_instance_id, user_response=trimmed)` — which also auto-stamps `updated_at`.
**Why**: The respondable notification prompt tells the main agent to call `respond_to_task`, but prompt instructions are only guidance — the agent could hallucinate a call against a non-waiting instance, be manipulated by injected content, or race with another response. The tool-level status + already-responded check is the authoritative gate: a call against the wrong row returns an error and changes nothing. This keeps `respond_to_task` safe to expose in every main-conversation turn; the tool itself is the enforcer, not the prompt.

**Consequences**:
- Pro: Prompt instructions can evolve without changing the safety envelope
- Pro: A second (race) response returns a clear "already pending" error instead of overwriting
- Pro: Calls against running/completed/failed instances are cleanly rejected
- Pro: Excluding the tool from background sessions closes the one gap the status guard cannot cover — a concurrent background task fabricating a response to another agent's waiting instance
- Con: Two lookups (get + update) per call; acceptable for an interactive operation
- Con: Two task-tools server instances must be built at startup — one with `respond_to_task`, one without — a small startup cost for clear capability scoping

### Corrupted definition auto-disable

**Choice**: When a repository list method encounters a record with an unparseable schedule, log a warning and disable the definition rather than failing the entire query.
**Why**: One corrupted definition blocked the entire instance generator loop (the list comprehension fails before per-definition error handling). Auto-disabling quarantines the bad record so it won't be re-encountered every tick, while allowing all valid definitions to continue scheduling. The user can see what happened via logs and re-create the task if needed. This is consistent with the one-shot auto-disable pattern already in the scheduler.

**Consequences**:
- Pro: One bad record cannot halt all task scheduling
- Pro: Corrupted definitions are quarantined rather than causing log spam
- Con: The definition is disabled (not deleted), so the user must manually clean up or re-create
- Con: If the disable write itself fails, it is logged and skipped (fail-open to preserve other definitions)

### Pending-instance handoff for on-demand execution

**Choice**: `run_task_now` creates a pending `TaskInstance` and returns immediately; the existing background task runner picks it up on its next tick (~30s).
**Why**: Pending instances are the canonical handoff to the runner. Every other instance-creation path (cron, one-shot) goes through this shape, so the runner's concurrency gating (semaphore), crash recovery, and queuing semantics apply uniformly. Calling into the executor directly would bypass the semaphore and require bespoke error handling.
**Consequences**:
- Pro: No new execution surface; the runner/executor code is untouched
- Pro: Uniform queuing — if `max_concurrent_background` is saturated, `run_task_now`-created instances wait alongside scheduler-created ones
- Pro: Crash recovery (`mark_running_as_failed`) covers them for free
- Con: Up to ~30s delay between tool call and task start

### Transient instances for ad-hoc mode

**Choice**: Ad-hoc `run_task_now` creates a transient `TaskInstance` with `definition_id=None` instead of a throwaway one-shot definition.
**Why**: `TaskInstance.definition_id` is already nullable — existing code (notifications) already uses `definition_id=None`. The executor's `notification_source` falls back gracefully to `prompt[:100]` when the definition lookup fails. Creating a throwaway definition would pollute the definitions table, require cleanup logic, and surface ephemeral entries in `list_tasks`.
**Consequences**:
- Pro: No definition-table pollution; `list_tasks` stays clean
- Pro: No cleanup/garbage-collection logic needed
- Pro: Reuses existing executor fallback path
- Con: Ad-hoc instances are not re-runnable by `task_id` — the caller re-issues the prompt

### Single Pydantic model with root validator for argument exclusivity

**Choice**: `RunTaskNowArgs` uses a single model with optional `task_id`, `prompt`, `name` and a `@model_validator` that enforces exactly one of `task_id` or `prompt`, with `name` only valid alongside `prompt`.
**Why**: Two separate arg models would require either two tool registrations or a `Union` schema — both add MCP schema complexity. A single model with a validator keeps the MCP tool signature simple (one tool, three optional fields, clear validation errors).
**Consequences**:
- Pro: Single MCP tool entry; simpler schema for the agent
- Pro: Error messages are explicit about what combination is valid
- Con: Slightly more complex Pydantic model (one validator) — negligible

### Auto-stamped `since` for stale-cron prevention

**Choice**: `TaskDefinitionRecord.since` uses SQLAlchemy Python-side `default=lambda: datetime.now(UTC)` and `onupdate=lambda: datetime.now(UTC)` (DES-009). The instance generator uses `since` as the earliest acceptable cron match — if CronSim's first match is before `since`, the anchor is advanced past `since` to find the next valid occurrence.
**Why**: Without `since`, creating or updating a cron task whose schedule time already passed today would immediately fire an instance to "catch up" instead of waiting for the next scheduled occurrence. For example: a task scheduled for 4 PM, updated at noon to run at 8 AM, would fire immediately instead of waiting until tomorrow. The auto-timestamp pattern means every write path (create, update, scheduler fire) refreshes `since` without caller involvement.

**Consequences**:
- Pro: Newly created cron tasks never fire retroactively for matches before creation time
- Pro: Schedule updates get a fresh anchor — the next occurrence after the update fires, not the stale past one
- Pro: Auto-stamped on every UPDATE via `onupdate`, so even prompt-only edits refresh the anchor
- Con: The scheduler's own `last_fired_at` update also refreshes `since` (via `onupdate`), but this is safe because after firing, the next CronSim anchor is the fire time and the next match is always in the future

Note: `since` is declared non-nullable (`Mapped[datetime]`) because it is introduced alongside this feature — no pre-existing rows need accommodation. This differs from `updated_at` on `TaskInstance`, which must handle legacy `NULL` rows from before that column was added.

### No cross-instance concurrency gate in run_task_now

**Choice**: `run_task_now` does not check for existing running/waiting instances — concurrency is bounded only by `max_concurrent_background` in the runner.
**Why**: `get_active_instance_for_definition` is deliberately not called (unlike the cron/one-shot paths in the scheduler, which use it for period-aware dedup). Adding a tool-level gate would create an asymmetric surface for an edge case; pause-then-rerun scenarios are expected to resolve the waiter first via `respond_to_task`.
**Consequences**:
- Pro: Simple handler; uniform with scheduler-created pending instances
- Pro: Semaphore already prevents resource exhaustion
- Con: Callers wanting serialization must coordinate externally

## System Behavior

### Scenario: Agent creates a recurring task

**Given**: The agent is in a conversation
**When**: It calls `create_task` with a cron schedule
**Then**: The task definition is persisted and instances will be generated when the schedule fires.

### Scenario: Instance generation for a cron task

**Given**: An enabled cron-based task definition exists
**When**: The cron match time has already passed
**Then**: A pending instance is created with `scheduled_for` set to the cron match time, and `last_fired_at` is updated.

### Scenario: One-shot task auto-disables

**Given**: An enabled one-shot task definition
**When**: The scheduled datetime passes and an instance is generated
**Then**: The definition is set to `enabled=false`.

### Scenario: Cron period-aware duplicate prevention

**Given**: A cron task where a pending, running, or completed instance already exists with `scheduled_for` matching the current cron match time
**When**: The instance generator evaluates the definition
**Then**: No new instance is created. Failed instances are excluded from this check to allow retry within the same period.

### Scenario: Stale-cron prevention on create

**Given**: A new cron task definition is created at noon with a schedule that matches 8 AM daily
**When**: The instance generator evaluates the definition
**Then**: The first CronSim match (today 8 AM) is before `since` (creation time). The generator advances the anchor past `since` and finds tomorrow 8 AM. No instance fires until tomorrow.

### Scenario: Stale-cron prevention on update

**Given**: An existing cron task scheduled for 4 PM is updated at noon with a new schedule for 8 AM
**When**: The instance generator evaluates the definition
**Then**: `since` is refreshed to the update time via `onupdate`. Today's 8 AM match is before `since`, so the generator advances to tomorrow 8 AM. No retroactive firing.

### Scenario: Catch-up after restart

**Given**: The system was down for multiple cron periods
**When**: The instance generator runs after restart
**Then**: At most one catch-up instance is created per definition. The generator evaluates each definition once per tick, and the `last_fired_at` update (set to wall-clock now) ensures subsequent ticks anchor CronSim past all missed periods.

### Scenario: Crash recovery on startup

**Given**: The application crashed while tasks were running
**When**: The bootstrap hook runs
**Then**: All previously-running instances are marked as `failed`.

### Scenario: Re-scheduling a disabled one-shot task

**Given**: A disabled one-shot task definition with `last_fired_at` set (it has fired previously)
**When**: The agent calls `update_task` with a new schedule and `enabled=true`
**Then**: `last_fired_at` is reset to `None` (because the schedule changed), the definition is re-enabled, and the instance generator will create a new pending instance when the new schedule fires.

### Scenario: Re-enabling a one-shot without new schedule

**Given**: A disabled one-shot task definition with `last_fired_at` set
**When**: The agent calls `update_task` with only `enabled=true` (no new schedule)
**Then**: `last_fired_at` remains set, the definition is re-enabled, but the instance generator will not fire it (the `last_fired_at is None` guard prevents stale one-shot schedules from firing).

### Scenario: Corrupted definition auto-disable

**Given**: An enabled task definition has a malformed schedule value in the database
**When**: The repository lists enabled definitions
**Then**: The corrupted definition is disabled (enabled=false), a warning is logged with the definition ID and error, and all other valid definitions are returned normally.

### Scenario: On-demand by-reference execution

**Given**: A background task definition exists (enabled or disabled)
**When**: The agent calls `run_task_now` with its `task_id`
**Then**: A pending `TaskInstance` is created with the definition's prompt snapshotted at call time. The definition's `enabled`, `last_fired_at`, and schedule are unchanged. The runner picks up the instance on its next tick.

### Scenario: On-demand ad-hoc execution

**Given**: The agent needs to run a one-off background task
**When**: The agent calls `run_task_now` with `prompt` (and optional `name`)
**Then**: A transient `TaskInstance` is created with `definition_id=None`. No `TaskDefinition` is persisted. The runner picks up the instance and the executor uses its `prompt[:100]` fallback for the notification source.

### Scenario: On-demand rejects session-type definition

**Given**: A session-type task definition exists
**When**: The agent calls `run_task_now` with its `task_id`
**Then**: The tool returns an error containing "Only background tasks support on-demand execution" and no instance is created.

### Scenario: On-demand with concurrent instance

**Given**: A running instance exists for a background task definition
**When**: The agent calls `run_task_now` with the same `task_id`
**Then**: A new pending instance is created. The runner's semaphore gates actual execution concurrency.

### Scenario: Fired one-shot is cleaned up after retention

**Given**: A one-shot task fired, auto-disabled, and its instance completed more than `cleanup_retention_hours` ago
**When**: The `one_shot_cleanup` Job fires (daily at 3 AM via DES-010)
**Then**: The definition and its instances are deleted in one transaction. Recurring cron definitions are never touched even if disabled with all instances terminal.

### Scenario: Cleanup blocked by in-flight instance

**Given**: A fired one-shot whose instance is still `running` or `waiting`
**When**: The cleanup Job fires
**Then**: The definition is preserved — the `NOT EXISTS` subquery excludes it. Cleanup will re-evaluate on the next daily run.

### Scenario: Cleanup of zero-instance fired one-shot

**Given**: A fired one-shot definition has `last_fired_at` older than the retention window but no instance rows (e.g. the instance was manually deleted, or the fire predates the instance schema)
**When**: The cleanup Job fires
**Then**: The definition is deleted — the retention anchor falls back to `last_fired_at` when no instance exists.

### Design decision: One-shot cleanup via Job + repository method

**Choice**: Run cleanup as a cron-triggered Job in the central scheduler (`"0 3 * * *"`), backed by a single repository method `cleanup_expired_one_shot_definitions(retention_hours)`. Retention is configurable via `TaskSettings.cleanup_retention_hours` (default 48h, ge=0).

**Why**: Daily cadence fits DES-010's `CronTrigger` naturally and keeps cleanup off the hot 60s instance-generator tick. Encapsulating find+delete in a single transactional repository method avoids N+1 queries and keeps the Job a one-liner. JSON substring match on `'"type": "once"'` is tightly coupled to `to_json()` serialization but acceptable given it's the only serializer and is covered by tests. The retention anchor composes `max(instance.completed_at)` with a `last_fired_at` fallback so zero-instance-but-fired definitions are also eligible — otherwise a partially corrupted state would accumulate forever.

**Consequences**:
- Pro: New cadences slot in without touching `instance_generator`; future system-maintenance operations register as additional Jobs
- Pro: Cleanup failures are isolated per DES-010 and log without affecting other jobs
- Con: JSON substring filter will need adjustment if `ScheduleConfig.to_json()` changes serialization

## Notes

- `cronsim` is used for cron expression evaluation (lightweight, timezone-aware)
- Task `type` is copied from definition to instance at creation time to enable direct queries without joins
- Background task notifications are agent-driven — the executor registers a `send_notification` MCP tool per background task execution (via DES-006 factory), and the agent calls it to deliver messages; failure notifications are automatic via the shared `dispatch_notification()` from `tachikoma.notifications`
- Background tasks that enter the `waiting` state are picked up on the next runner tick once `user_response` is set (see [background-task-execution design](background-task-execution.md)). The `respond_to_task` tool — registered here on the task-tools MCP server — is the write-side of that handoff; the runner's `get_ready_background_instances()` query is the read-side. No direct coupling exists between the two subsystems beyond the instance row
- Task MCP tools ("task-tools" server) and workflow MCP tools ("workflow-tools" server) are distinct systems registered independently in the coordinator's `mcp_servers`. Task tools manage cron-scheduled definitions; workflow tools manage ordered step sequences within skills. See [workflows design](../workflows/workflow-state-machine.md).
