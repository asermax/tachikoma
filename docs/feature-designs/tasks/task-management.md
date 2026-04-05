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
| `src/tachikoma/tasks/model.py` | `TaskDefinition` and `TaskInstance` frozen dataclasses (domain types); `TaskDefinitionRecord` and `TaskInstanceRecord` ORM models; `TaskStatus` and `TaskType` constant maps; `ScheduleConfig` type | Domain types frozen; ORM models internal to persistence; schedule stored as JSON column |
| `src/tachikoma/tasks/repository.py` | `TaskRepository` — async SQLAlchemy CRUD for definitions and instances; `list_enabled_definitions()` and `list_disabled_definitions()` for filtered queries; crash recovery (mark running as failed) | Receives shared `async_sessionmaker` from `Database`; follows ADR-007 pattern |
| `src/tachikoma/tasks/tools.py` | `create_task_tools_server(repository, timezone)` — MCP server factory receiving `ZoneInfo` at construction; `_parse_schedule(schedule, tz)` stamps naive datetimes with configured timezone, preserves aware as-is; `_format_schedule(schedule, tz)` converts display to configured timezone; `list_tasks`, `create_task`, `update_task`, `delete_task` with `cronsim` validation; Pydantic `BaseModel` classes (`ListTasksArgs`, `CreateTaskArgs`, `UpdateTaskArgs`, `DeleteTaskArgs`) for arg validation | Factory receives `ZoneInfo`, passes to `_parse_schedule` and `_format_schedule` via closures; uses `replace(tzinfo=tz)` for naive, `astimezone(tz)` for display; follows DES-006 |
| `src/tachikoma/tasks/hooks.py` | `tasks_hook` — bootstrap hook (DES-003): retrieves shared `Database` from extras, creates repository, runs crash recovery; stores `task_repository` in `bootstrap.extras` | Subsystem-owned hook; runs after `database_hook` |
| `src/tachikoma/tasks/scheduler.py` | `instance_generator()` — async loop evaluating definitions via `cronsim`; `get_timezone(settings)` — returns `ZoneInfo` from pre-validated settings string (shared utility used by scheduler, preamble rendering, and executor) | Plain async function started as `asyncio.Task`; `get_timezone` has no fallback logic — validation happens at config load |
| `src/tachikoma/database.py` | Shared `Database` class with `Base(DeclarativeBase)`, `AsyncEngine`, `async_sessionmaker`; `database_hook` bootstrap hook | All ORM models share one `Base`; single engine for all subsystems |
| `src/tachikoma/context/loading.py` (`SYSTEM_PREAMBLE_TEMPLATE`) | Timezone-aware tasks documentation in the system prompt preamble: task types, scheduling formats with timezone behavior, Date and Time section, MCP tools, and notify field | `SYSTEM_PREAMBLE_TEMPLATE` with `{timezone}` placeholder; `render_system_preamble(timezone)` resolves and formats; follows ADR-008 append pattern |

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
- MCP tool errors: return `{"is_error": true, "content": [...]}` — agent sees error message and can retry
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
├── notify: str | None               (notification template, null = silent)
├── enabled: bool                    (default True)
├── last_fired_at: datetime | None   (last time an instance was generated)
└── created_at: datetime             (creation timestamp)
```

### TaskInstance

```
TaskInstance (frozen dataclass)
├── id: str                          (UUID)
├── definition_id: str | None        (FK → task_definitions.id, null for transient)
├── task_type: str                   ("session" or "background", copied from definition)
├── status: str                      ("pending", "running", "completed", "failed")
├── prompt: str                      (copied from definition at creation time)
├── scheduled_for: datetime          (when the instance should execute)
├── started_at: datetime | None      (when execution began)
├── completed_at: datetime | None    (when execution finished)
├── result: str | None               (completion/failure summary)
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
        string notify
        boolean enabled
        datetime last_fired_at
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
        datetime created_at
    }
```

Note: `TaskInstance.definition_id` is nullable — transient instances (notifications from background task results) have no parent definition.

### Task status lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: instance created
    pending --> running: execution starts
    running --> completed: evaluator marks done
    running --> failed: stuck/error/max iterations
    completed --> [*]
    failed --> [*]
```

## Data Flow

### Instance generation flow

```
1. Instance generator loop wakes up (~60s interval)
2. Query all enabled definitions from repository
3. For each definition:
   a. Parse schedule: CronSim(expr, anchor_time, tz=configured_timezone)
   b. Check if next fire time ≤ now
   c. If yes, check no pending/running instance exists for this definition
   d. If clear, create TaskInstance(status="pending", task_type=definition.task_type, scheduled_for=fire_time)
   e. Update definition.last_fired_at = now
   f. If one-shot, set definition.enabled = false
4. Sleep until next tick
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
5. Returns formatted list or "No active/archived tasks found."
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

**Choice**: Include task types, scheduling formats, tool descriptions, and the notify field in `SYSTEM_PREAMBLE` as a static Tasks section.
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

## System Behavior

### Scenario: Agent creates a recurring task

**Given**: The agent is in a conversation
**When**: It calls `create_task` with a cron schedule
**Then**: The task definition is persisted and instances will be generated when the schedule fires.

### Scenario: Instance generation for a cron task

**Given**: An enabled cron-based task definition exists
**When**: The cron expression matches the current time
**Then**: A pending instance is created and `last_fired_at` is updated.

### Scenario: One-shot task auto-disables

**Given**: An enabled one-shot task definition
**When**: The scheduled datetime passes and an instance is generated
**Then**: The definition is set to `enabled=false`.

### Scenario: Crash recovery on startup

**Given**: The application crashed while tasks were running
**When**: The bootstrap hook runs
**Then**: All previously-running instances are marked as `failed`.

## Notes

- `cronsim` is used for cron expression evaluation (lightweight, timezone-aware)
- Task `type` is copied from definition to instance at creation time to enable direct queries without joins
- The `notify` field on `TaskDefinition` is a nullable instruction string — when set, the background task executor uses it to generate a notification message on completion
