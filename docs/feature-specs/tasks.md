# Tasks

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The tasks extension gives Tachikoma scheduled work. Persistent task definitions describe what the agent should do and when — a recurring cron expression or a one-shot ISO datetime — and task instances represent individual firings generated from those definitions. Session instances are delivered into the conversation as idle-gated proactive messages; background instances run autonomously through an evaluator loop in ephemeral pi side sessions.

The agent manages definitions conversationally through registered tools (`create_task`, `update_task`, `list_tasks`, `query_task_instances`). All scheduling state lives in SQLite, so definitions and pending instances survive restarts.

## User Stories

- As a user, I want to schedule one-shot or recurring work conversationally so that Tachikoma reminds me and follows up without me triggering every action
- As a user, I want background work to run autonomously and to be told the outcome so that long tasks don't block our conversation
- As a user, I want scheduled work to survive restarts so that missed runs are caught up rather than silently dropped

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Persistent task definitions with a cron or one-shot schedule and a `session`/`background` type; definitions and instances are stored in SQLite (`task_definitions`, `task_instances`) and survive restarts |
| R1 | Schedule strings are parsed with croner: cron expressions stay recurring; bare ISO datetimes are interpreted in the configured timezone; explicit offsets (including `Z`) are preserved; one-shot datetimes in the past are rejected |
| R2 | A single scheduler tick (every 60 s) runs four passes in order: instance generation, waiting-instance expiration, session-task delivery, background dispatch |
| R3 | Cron generation anchors on `lastFiredAt`, so at most one catch-up instance per definition fires after downtime; a never-fired definition anchors at the start of the current hour |
| R4 | Stale-cron prevention: a `since` timestamp stamped on every definition insert and update prevents cron occurrences that predate the definition's latest edit from firing |
| R5 | Duplicate prevention: a cron firing is suppressed when a pending/running/waiting/completed instance already covers the same occurrence (failed excluded so retry stays possible); a one-shot is suppressed by any active instance |
| R6 | One-shot definitions auto-disable after firing |
| R7 | Instance lifecycle: `pending → running → waiting \| completed \| failed`; a crash-recovery bootstrap hook marks instances left `running` by a previous process as `failed` |
| R8 | Pending session instances are handed to channel delivery with `gate: "idle"` and a configurable max hold (`sessionTaskMaxHoldSeconds`, default 900); a failed handoff rolls the instance back to `pending` for retry on the next tick |
| R9 | Pending background instances run through an iterative evaluator loop in ephemeral in-memory pi sessions with filesystem and bash tools, capped at `backgroundMaxIterations` (default 10) |
| R10 | After each background iteration a classifier evaluates the response as `complete`, `continue`, or `error`; an evaluator failure is treated as `continue` and never aborts the run |
| R11 | Background completion delivers the agent's final text to the user idle-gated; failures (evaluator `error`, max iterations, thrown errors) mark the instance `failed` and deliver a failure notice |
| R12 | Background completion and failure additionally emit a structured status payload on the `notify` app event (source, instance ID, status, message) for cross-extension consumers |
| R13 | Waiting instances whose last update is older than `waitTimeoutSeconds` (default 7200) are marked `failed` and a failure notice is delivered |
| R14 | Agent-facing tools: `create_task`, `update_task` (partial updates; `enabled=false` archives instead of deleting; a schedule change resets `lastFiredAt`), `list_tasks` (active by default, `archived=true` for disabled), `query_task_instances` (filter by status, type, definition) |
| R15 | A definition whose schedule cannot be evaluated is skipped with an error log; the generation pass continues with the remaining definitions |
| R16 | In-flight background executions are tracked across ticks so a slow run is never dispatched twice |

## Behaviors

### Definitions and Scheduling (R0, R1, R6)

Definitions pair an agent prompt with a parsed schedule; the stored form is a discriminated union (`cron` expression or `once` ISO instant).

**Acceptance Criteria**:
- Given the agent creates a task with `schedule: "0 9 * * *"`, when the definition is stored, then it is a recurring cron schedule, enabled, with `lastFiredAt = null`
- Given a bare datetime like `2026-07-01T15:00:00` and a configured timezone, when parsed, then the stored one-shot instant reflects that timezone; an explicit offset or `Z` is preserved as-is
- Given a one-shot datetime in the past or an unparseable schedule string, when the agent creates or updates a task, then the tool fails with a clear error and nothing is stored
- Given a due one-shot fires, then its definition is set `enabled = false` and further ticks create nothing

### Instance Generation (R2, R3, R4, R5, R15)

Each tick evaluates every enabled definition against the current time and creates pending instances for due schedules.

**Acceptance Criteria**:
- Given an enabled cron definition whose next occurrence after `lastFiredAt` has passed, when the tick runs, then one pending instance is created with `scheduledFor` set to that occurrence and `lastFiredAt` advances to now
- Given a cron definition created after today's occurrence already passed (e.g. daily-at-10:00 created at 10:30), when ticks run, then nothing fires until the next occurrence after `since`
- Given an instance already covers the occurrence (any status except `failed`), when the tick runs, then no duplicate is created — even if `lastFiredAt` was rewound
- Given one definition with an invalid cron expression among valid ones, when the tick runs, then the invalid one is skipped and logged and the valid ones still generate

### Session Task Delivery (R8)

Pending session instances are injected into the conversation as agent turns through the channel delivery gate (gating semantics in [conversation-loop.md](conversation-loop.md)).

**Acceptance Criteria**:
- Given a pending session instance, when the delivery pass runs, then `app.channels.deliver` is called with the rendered task text (a "Scheduled task" label plus the prompt), `gate: "idle"`, `target: "agent"`, the configured max hold, and `metadata { kind: "session_task", instanceId }`, and the instance is marked `completed`; the coordinator injects the `agent`-targeted delivery as a fresh turn the agent acts on
- Given the delivery handoff throws, then the instance is rolled back to `pending` with `startedAt` cleared and is retried on the next tick
- Given pending background instances, when the session delivery pass runs, then they are not delivered

### Background Execution (R9, R10, R11, R12, R16)

Background instances execute autonomously: each iteration runs an ephemeral headless pi session, then a classifier decides whether the workflow is finished.

**Acceptance Criteria**:
- Given a pending background instance, when the runner dispatches it, then it is marked `running` and the agent runs with a background system prompt that includes the current date and time in the configured timezone
- Given the evaluator returns `continue`, then the next iteration's prompt carries the original task, an excerpt of the previous response, and the evaluator's observation
- Given the evaluator returns `complete`, then the instance is marked `completed` with the evaluator's reason as result, the final agent text is delivered idle-gated, and a `completed` status payload is emitted on the `notify` event
- Given the evaluator returns `error`, the iteration cap is reached, or the run throws, then the instance is marked `failed` and a failure notice is delivered alongside a `failed` status payload
- Given a run spans multiple ticks, then the runner does not dispatch the same instance twice

### Expiration and Crash Recovery (R7, R13)

**Acceptance Criteria**:
- Given a `waiting` instance whose `updatedAt` is older than the wait timeout, when the expiration pass runs, then it is marked `failed` with a timeout reason and a failure notice is delivered; fresher waiting instances and non-waiting instances are untouched
- Given instances were left `running` when the process died, when the crash-recovery bootstrap hook runs at startup, then they are marked `failed` with a restart reason

### Task Tools (R14)

The task management tools are registered into every conversational agent session via `app.agent.use` (DES-001).

**Acceptance Criteria**:
- Given the agent calls `update_task` with a new schedule, then `lastFiredAt` resets to null so the new schedule is treated as fresh; updates without a schedule leave it untouched
- Given the agent calls `list_tasks`, then enabled definitions are listed with ID, name, type, schedule, and last-fired time (formatted in the configured timezone); `archived=true` lists disabled definitions instead
- Given the agent calls `query_task_instances` with filters, then matching instances are listed newest-first with status, schedule time, parent definition, and result
