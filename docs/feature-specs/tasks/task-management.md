# Task Management

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Persistent task definitions with cron-like scheduling, automatic instance generation, and MCP tools for the agent to manage tasks during conversations. Task definitions describe what the agent should do and when; task instances represent individual executions generated from those definitions.

Note: The workflow subsystem provides a separate set of MCP tools (`start_workflow`, `update_workflow_state`, etc.) registered as the "workflow-tools" server for managing multi-step skill processes. Task tools manage cron-scheduled definitions; workflow tools manage ordered step sequences within skills. See [workflows](../workflows/workflow-state-machine.md).

## User Stories

- As a user, I want to ask Tachikoma to do something on a schedule so that it proactively reminds me, processes information, and follows up without me having to manually trigger every action
- As a user, I want Tachikoma to manage task definitions (create, list, update, delete) through natural conversation so that scheduling feels like talking to an assistant
- As a user, I want to trigger a background task immediately (either an existing definition or a one-off prompt) without waiting for its schedule so that urgent work gets done on demand

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Persistent task definitions with cron expression scheduling and one-shot datetime support |
| R1 | MCP tools for the agent to list, get details, create, update, and delete task definitions during conversations |
| R2 | Automatic instance generation from enabled definitions when their schedule fires |
| R3 | Task instance status tracking (pending, running, completed, failed) |
| R4 | One-shot task definitions auto-disable after single execution |
| R5 | Duplicate instance prevention — cron tasks deduplicate per cron period (including completed); one-shot tasks check pending/running only |
| R6 | Catch-up on missed schedules after restart using `last_fired_at` |
| R7 | Task definitions and instances survive restarts (persistent storage) |
| R8 | Bootstrap step to initialize task database tables and run crash recovery |
| R9 | Base system prompt preamble includes a Tasks section (rendered with the configured timezone) so the agent has foundational awareness of the task system regardless of whether tasks currently exist |
| R10 | Schedule deserialization is robust to malformed data — legacy bare ISO datetime strings are recovered as one-shot schedules, and corrupted definitions are auto-disabled |
| R11 | On-demand background task execution via `run_task_now` MCP tool — supports both by-reference (existing definition) and ad-hoc (transient instance with no definition) modes |
| R12 | Stale-cron prevention — a `since` timestamp auto-stamped on every create and update prevents newly-created or recently-updated task definitions from firing retroactively for cron matches before the definition was last modified |
| R13 | Auto-cleanup of fired one-shot definitions — once the retention window has elapsed since the one-shot's last terminal instance (or since `last_fired_at` for zero-instance definitions), the definition and any associated instances are deleted. Recurring cron definitions are never cleaned up. |

## Behaviors

### Task Definition CRUD (R1)

The agent manages task definitions through MCP tools exposed during conversations. Tools validate input and return clear error messages.

**Acceptance Criteria**:
- Given the agent is in a conversation, when it calls `create_task` with a valid cron expression, type, prompt, and name, then a task definition is persisted with `enabled=true` and `last_fired_at=null`
- Given the agent calls `create_task` with a one-shot schedule, then a task definition is created that will fire exactly once
- Given the agent calls `create_task` with a one-shot schedule in the past, then the tool returns a clear error without creating a definition
- Given the agent calls `create_task` with a bare ISO datetime (no timezone info), then the datetime is interpreted in the user's configured timezone (not UTC)
- Given the agent calls `create_task` with an ISO datetime including an explicit timezone offset or `Z` suffix, then the explicit timezone is preserved as-is
- Given the agent calls `create_task` with an invalid cron expression, then the tool returns a clear error message
- Given the agent calls `create_task` without a required field (name, schedule, type, or prompt), then the tool returns a clear error identifying the missing field
- Given the agent calls `create_task` with a type value other than "session" or "background", then the tool returns a clear error
- Given the agent calls `list_tasks` with no arguments, then it receives only enabled task definitions with their task ID, current status, type, schedule (one-shot times displayed in the configured timezone), and last_fired_at information (displayed in the configured timezone). Prompts are not included — use `get_task` for full details
- Given the agent calls `list_tasks` with `archived=true`, then it receives only disabled task definitions
- Given the agent calls `list_tasks` and no matching definitions exist (no enabled tasks by default, or no disabled tasks when archived), then a clear "no tasks found" message is returned
- Given the agent calls `get_task` with a valid task ID, then it receives full details including the complete prompt, schedule, status, and timestamps (all displayed in the configured timezone)
- Given the agent calls `get_task` with an unknown task ID, then a clear "not found" error is returned
- Given the agent calls `update_task` with a modified schedule, prompt, task_type, or other updatable field, then the definition is updated and future instances use the new configuration
- Given the agent calls `update_task` with a modified schedule, then `last_fired_at` is reset to null so the instance generator treats the definition as fresh — enabling re-scheduling of disabled one-shot tasks
- Given the agent calls `update_task` with `enabled=true` but no new schedule, then `last_fired_at` is preserved — re-enabling without a new schedule does not cause a stale one-shot schedule to fire
- Given the agent calls `update_task` with a one-shot schedule in the past, then the tool returns a clear error (consistent with `create_task`)
- Given the agent calls `delete_task`, then the definition is removed and no further instances are generated

### Instance Generation (R2, R4, R5, R6)

An async loop continuously evaluates enabled definitions and creates pending instances when schedules fire. Cron expressions are evaluated in the user's configured timezone.

**Acceptance Criteria**:
- Given an enabled cron-based task definition, when the cron expression matches a time that has already passed, then a new task instance with status `pending` is created and the definition's `last_fired_at` is updated
- Given an enabled one-shot task definition, when the scheduled datetime has passed, then a single task instance is created and the definition is set to `enabled=false`
- Given a disabled task definition, then no instances are generated regardless of schedule
- Given a cron task definition where a pending, running, or completed instance already exists for the current cron period, then no duplicate instance is created; failed instances are excluded to allow retry within the same period
- Given a one-shot task definition that already has a pending or running instance, then no duplicate instance is created
- Given the system restarts, then the instance generator resumes and creates at most one catch-up instance per definition that was missed during downtime (using `last_fired_at` to determine what was missed)
- Given cron expressions are evaluated, then they use the user's configured timezone (via `cronsim` + stdlib `zoneinfo`)

### Stale-Cron Prevention (R12)

The instance generator uses a `since` timestamp (auto-stamped on every INSERT and UPDATE via SQLAlchemy) to prevent cron matches from before the definition was last modified from triggering instances.

**Acceptance Criteria**:
- Given a newly created cron task definition, when the cron match time is before the creation timestamp (`since`), then no instance is created and the generator waits for the next occurrence after creation
- Given an updated cron task definition, when the cron match time is before the update timestamp (`since`), then no instance is created
- Given a cron task where the first cron match is before `since`, the generator advances the CronSim anchor past `since` to find the next valid occurrence rather than skipping the task entirely
- Given a cron task that has just fired, when the next generator tick evaluates the definition, then the updated `since` timestamp does not prevent the next naturally-occurring cron match from firing

### One-Shot Cleanup (R13)

Fired one-shot task definitions accumulate in the database once they auto-disable. A daily cleanup pass deletes one-shots whose retention window has elapsed, along with their associated instances. Recurring cron definitions are excluded.

**Acceptance Criteria**:
- Given a one-shot definition with `last_fired_at` set and all instances in terminal status, when the latest completion is older than the configured retention window, then the definition and its instances are deleted atomically
- Given a one-shot definition with any pending, running, or waiting instance, then cleanup is blocked regardless of retention
- Given a one-shot definition whose latest instance completed within the retention window, then the definition is preserved
- Given a never-fired one-shot definition (`last_fired_at IS NULL`), then the definition is preserved regardless of age
- Given a cron-based definition, then it is never cleaned up even if disabled with all instances terminal
- Given a one-shot definition that fired but generated no instance rows, when `last_fired_at` is older than the retention window, then the definition is deleted
- Given the retention window is changed via `cleanup_retention_hours`, then subsequent cleanup passes honor the new value
- Given cleanup encounters an error, then the error is logged and the scheduler continues running other jobs

### Persistence and Recovery (R7, R8)

Task data survives restarts. The bootstrap hook initializes the database and performs crash recovery.

**Acceptance Criteria**:
- Given the application starts for the first time, then the bootstrap step creates the task tables in the shared database
- Given the application restarts, then all previously created task definitions and pending instances are available
- Given the application shuts down gracefully, then the background task runner cancels running executions, which mark their instances as `failed` with a cancellation reason; any instances not cleanly marked are caught by crash recovery on next startup
- Given the system crashed, when the bootstrap hook runs, then all previously-running instances are marked as `failed` (crash recovery)

### Preamble Awareness (R9)

The base system prompt preamble includes a timezone-aware Tasks section that gives the agent foundational awareness of the task system.

**Acceptance Criteria**:
- Given the system prompt is assembled, then the preamble Tasks section describes task types (session and background) and when to use each
- Given the preamble Tasks section, then it explains scheduling formats (cron expressions and ISO datetimes) including timezone behavior (bare datetimes interpreted in configured timezone, explicit offsets preserved)
- Given the preamble Tasks section, then it includes a Date and Time subsection showing the configured timezone and a date command for current time lookup
- Given the preamble Tasks section, then it lists each MCP tool with parameter types, required/optional indicators, valid values, and behavioral notes — including `run_task_now` with its two modes (by-reference and ad-hoc) and background-only restriction
- Given the preamble Tasks section, then tool descriptions include cross-references between tools (e.g., "get task IDs from list_tasks" for update_task and delete_task)
- Given the preamble Tasks section, then it states that background tasks can send notifications to the user during execution via the `send_notification` tool, and that failures are automatically notified

### Schedule Deserialization Robustness (R10)

Schedule deserialization handles malformed data gracefully, preventing corrupted definitions from blocking the entire task scheduler.

**Acceptance Criteria**:
- Given a task definition with a bare ISO datetime string in the schedule column (legacy format), when `from_json` is called, then it recovers the value as a one-shot schedule with UTC defaulting for naive datetimes (legacy data recovery, distinct from `create_task` timezone stamping for new user input)
- Given a task definition with completely invalid data in the schedule column, when `from_json` is called, then it raises `ValueError` (not `JSONDecodeError`) with the malformed input in the message
- Given a task definition with structurally valid JSON but an unexpected type (e.g., array), when `from_json` is called, then it raises `ValueError` describing the unexpected type
- Given multiple enabled definitions where one has a corrupted schedule, when the repository lists enabled definitions, then valid definitions are returned and the corrupted definition is auto-disabled (logged as warning)

### On-demand Execution (R11)

The agent can trigger a background task immediately via the `run_task_now` MCP tool, bypassing the scheduler. The tool supports two modes: by-reference (re-run an existing background definition) and ad-hoc (fire a one-off prompt without creating a definition). Both modes create a pending `TaskInstance` that the existing background task runner picks up on its next tick.

**Acceptance Criteria**:
- Given a background task definition exists, when the agent calls `run_task_now` with its `task_id`, then a new pending `TaskInstance` is created with `definition_id` set to the task ID, `prompt` snapshotted from the definition, `task_type="background"`, and all execution-state fields null. The tool response includes the new instance ID. The definition's `enabled`, `last_fired_at`, and schedule remain unchanged.
- Given a pending instance created by `run_task_now`, when the runner's next tick runs, then the instance transitions through the standard `pending → running → completed|failed|waiting` lifecycle without modifying the parent definition.
- Given a disabled background task definition (e.g., an auto-disabled one-shot), when the agent calls `run_task_now`, then a pending instance is created and the definition stays disabled with `last_fired_at` untouched.
- Given a session-type task definition, when the agent calls `run_task_now` with `task_id`, then the tool returns an error containing "Only background tasks support on-demand execution" and no instance is created.
- Given an unknown `task_id`, when the agent calls `run_task_now`, then the tool returns a "not found" error.
- Given the agent calls `run_task_now` with both `task_id` and `prompt`, or with neither, then the tool returns a validation error and no instance is created. `name` is only valid with `prompt`.
- Given the agent calls `run_task_now` with only `prompt` (and optional `name`), then a transient `TaskInstance` is created with `definition_id=None` and the caller-supplied prompt. No `TaskDefinition` is persisted. The instance is picked up by the runner and completes the standard lifecycle.
- Given the agent calls `run_task_now` with `prompt` and `name`, then the instance is created as above, and `name` appears in the log as the source label.
- Given a running or waiting instance for the same definition already exists, when the agent calls `run_task_now`, then a new pending instance is still created — the tool performs no cross-instance concurrency check; gating is left to the runner's `max_concurrent_background` semaphore.
- Given a successful call, the tool emits an info-level log line including the instance ID, the mode (`by_ref` or `ad_hoc`), and the source identifier (definition ID + name for by-ref; `name` or prompt preview for ad-hoc).
- Given the background task-tools server (without `respond_to_task`), when it is inspected, then `run_task_now` is present and behaves identically to the main-server copy.

## Requires

Dependencies:
- None

Assumes existing:
- Configuration system with `[tasks]` section for scheduler parameters (config-system)
- Bootstrap hook system (DES-003)
- Persistence layer pattern (ADR-007)
