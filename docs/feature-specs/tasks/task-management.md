# Task Management

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Persistent task definitions with cron-like scheduling, automatic instance generation, and MCP tools for the agent to manage tasks during conversations. Task definitions describe what the agent should do and when; task instances represent individual executions generated from those definitions.

## User Stories

- As a user, I want to ask Tachikoma to do something on a schedule so that it proactively reminds me, processes information, and follows up without me having to manually trigger every action
- As a user, I want Tachikoma to manage task definitions (create, list, update, delete) through natural conversation so that scheduling feels like talking to an assistant

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Persistent task definitions with cron expression scheduling and one-shot datetime support |
| R1 | MCP tools for the agent to list, create, update, and delete task definitions during conversations |
| R2 | Automatic instance generation from enabled definitions when their schedule fires |
| R3 | Task instance status tracking (pending, running, completed, failed) |
| R4 | One-shot task definitions auto-disable after single execution |
| R5 | Duplicate instance prevention — no new instance if one is already pending or running for the same definition |
| R6 | Catch-up on missed schedules after restart using `last_fired_at` |
| R7 | Task definitions and instances survive restarts (persistent storage) |
| R8 | Bootstrap step to initialize task database tables and run crash recovery |
| R9 | Base system prompt preamble includes a static Tasks section so the agent has foundational awareness of the task system regardless of whether tasks currently exist |

## Behaviors

### Task Definition CRUD (R1)

The agent manages task definitions through MCP tools exposed during conversations. Tools validate input and return clear error messages.

**Acceptance Criteria**:
- Given the agent is in a conversation, when it calls `create_task` with a valid cron expression, type, prompt, and name, then a task definition is persisted with `enabled=true` and `last_fired_at=null`
- Given the agent calls `create_task` with a one-shot schedule, then a task definition is created that will fire exactly once
- Given the agent calls `create_task` with a one-shot schedule in the past, then the tool returns a clear error without creating a definition
- Given the agent calls `create_task` with an invalid cron expression, then the tool returns a clear error message
- Given the agent calls `create_task` without a required field (name, schedule, type, or prompt), then the tool returns a clear error identifying the missing field
- Given the agent calls `create_task` with a type value other than "session" or "background", then the tool returns a clear error
- Given the agent calls `list_tasks` with no arguments, then it receives only enabled task definitions with their task ID, current status, type, schedule, and last_fired_at information
- Given the agent calls `list_tasks` with `archived=true`, then it receives only disabled task definitions
- Given the agent calls `list_tasks` and no matching definitions exist (no enabled tasks by default, or no disabled tasks when archived), then a clear "no tasks found" message is returned
- Given the agent calls `update_task` with a modified schedule, prompt, task_type, or other updatable field, then the definition is updated and future instances use the new configuration
- Given the agent calls `delete_task`, then the definition is removed and no further instances are generated

### Instance Generation (R2, R4, R5, R6)

An async loop continuously evaluates enabled definitions and creates pending instances when schedules fire. Cron expressions are evaluated in the user's configured timezone.

**Acceptance Criteria**:
- Given an enabled cron-based task definition, when the cron expression matches the current time, then a new task instance with status `pending` is created and the definition's `last_fired_at` is updated
- Given an enabled one-shot task definition, when the scheduled datetime has passed, then a single task instance is created and the definition is set to `enabled=false`
- Given a disabled task definition, then no instances are generated regardless of schedule
- Given a definition already has a pending or running instance, then no duplicate instance is created for the same definition
- Given the system restarts, then the instance generator resumes and creates at most one catch-up instance per definition that was missed during downtime (using `last_fired_at` to determine what was missed)
- Given cron expressions are evaluated, then they use the user's configured timezone (via `cronsim` + stdlib `zoneinfo`)

### Persistence and Recovery (R7, R8)

Task data survives restarts. The bootstrap hook initializes the database and performs crash recovery.

**Acceptance Criteria**:
- Given the application starts for the first time, then the bootstrap step creates the task tables in the shared database
- Given the application restarts, then all previously created task definitions and pending instances are available
- Given the application shuts down gracefully, then the background task runner cancels running executions, which mark their instances as `failed` with a cancellation reason; any instances not cleanly marked are caught by crash recovery on next startup
- Given the system crashed, when the bootstrap hook runs, then all previously-running instances are marked as `failed` (crash recovery)

### Preamble Awareness (R9)

The base system prompt preamble includes a static Tasks section that gives the agent foundational awareness of the task system.

**Acceptance Criteria**:
- Given the system prompt is assembled, then the preamble Tasks section describes task types (session and background) and when to use each
- Given the preamble Tasks section, then it explains scheduling formats (cron expressions and ISO datetimes)
- Given the preamble Tasks section, then it lists each MCP tool with parameter types, required/optional indicators, valid values, and behavioral notes
- Given the preamble Tasks section, then tool descriptions include cross-references between tools (e.g., "get task IDs from list_tasks" for update_task and delete_task)
- Given the preamble Tasks section describes the `notify` parameter, then it states that `notify` is a success notification instruction — when set, generates a user-facing message on completion; when omitted, successful tasks complete silently; failures always notify regardless of this field

## Requires

Dependencies:
- None

Assumes existing:
- Configuration system with `[tasks]` section for scheduler parameters (config-system)
- Bootstrap hook system (DES-003)
- Persistence layer pattern (ADR-007)
