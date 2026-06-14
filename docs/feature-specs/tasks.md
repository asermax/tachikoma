# Tasks

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The tasks extension gives Tachikoma scheduled work. Persistent task definitions describe what the agent should do and when — a recurring cron expression or a one-shot ISO datetime — and task instances represent individual firings generated from those definitions. Session instances are queued into the conversation as proactive agent turns (Normal tier); background instances run autonomously through an evaluator loop on a single persistent pi session that spans every iteration and survives an `ask_user` pause. The extension also contributes a usage context section (scope: main + background) describing the task model, schedule formats, and tools.

The agent manages definitions conversationally through registered tools (`create_task`, `update_task`, `get_task`, `delete_task`, `list_tasks`, `run_task_now`, `query_task_instances`). All scheduling state lives in SQLite, so definitions and pending instances survive restarts.

## User Stories

- As a user, I want to schedule one-shot or recurring work conversationally so that Tachikoma reminds me and follows up without me triggering every action
- As a user, I want background work to run autonomously and to be told the outcome so that long tasks don't block our conversation
- As a user, I want scheduled work to survive restarts so that missed runs are caught up rather than silently dropped

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Persistent task definitions with a cron or one-shot schedule and a `session`/`background` type; definitions and instances are stored in SQLite (`task_definitions`, `task_instances`) and survive restarts |
| R1 | Schedule strings are parsed with croner: cron expressions stay recurring; bare ISO datetimes are interpreted in the configured timezone; explicit offsets (including `Z`) are preserved; one-shot datetimes in the past are rejected |
| R2 | A single scheduler tick (every 60 s) runs five passes in order: instance generation, waiting-instance expiration, stuck-running sweep, session-task delivery, background dispatch (the background dispatch pass resumes answered `waiting` instances ahead of fresh `pending` ones) |
| R3 | Cron generation anchors on `lastFiredAt`, so at most one catch-up instance per definition fires after downtime; a never-fired definition anchors at the start of the current hour |
| R4 | Stale-cron prevention: a `since` timestamp stamped on every definition insert and update prevents cron occurrences that predate the definition's latest edit from firing |
| R5 | Duplicate prevention: a cron firing is suppressed when a pending/running/waiting/completed instance already covers the same occurrence (failed excluded so retry stays possible); a one-shot is suppressed by any active instance |
| R6 | One-shot definitions auto-disable after firing |
| R7 | Instance lifecycle: `pending → running → (waiting → running)* → completed \| failed`. A background run can suspend mid-execution into `waiting` via the `ask_user` tool and resume back to `running` once answered; a crash-recovery bootstrap hook marks instances left `running` by a previous process as `failed` |
| R8 | Pending session instances are transitioned to `running` (with `startedAt` stamped), then handed to channel delivery at the Normal `tier`; a successful handoff marks the instance `completed`, while a failed handoff rolls it back to `pending` (clearing `startedAt`) for retry on the next tick |
| R9 | Pending background instances run through an iterative evaluator loop on a single persistent pi session opened once per execution, capped at `backgroundMaxIterations` (default 10); the session is prompted repeatedly across iterations (retaining its own history) and is disposed on every exit path; a background run uses the main conversational session's tool model (no hard tool allowlist) so its bound extension tools stay active alongside the built-in filesystem/bash tools, the `ask_user` custom tool, and the notifications extension's background-scoped `notify_user` |
| R9a | A background run binds a curated subset of extension factories — those opted in via `app.agent.use(factory, { background: true })`: skills (workspace skill sources + `delegate_to_agent`, including the built-in general-purpose subagent), git, projects, detached-processes, notifications, and the task tools — so an autonomous task has the capabilities it needs; interactive/channel-only or risky surfaces (telegram, self-update, external) are deliberately not bound |
| R9b | A background run receives workspace context (memory indexes, projects, subsystem usage) through the background-scoped extension context sections it binds — each injected once via pi's `before_agent_start` — not folded into the task prompt; the opening prompt is just the task itself |
| R10 | After each background iteration a classifier evaluates the response as `complete`, `continue`, or `error`; an evaluator failure is treated as `continue` and never aborts the run |
| R11 | Background completion does **not** deliver a programmatic notice — the agent decides whether to surface results by calling `notify_user` (per the task's own prompt guidance); the instance result is still stored. Failures (evaluator `error`, max iterations, thrown errors) mark the instance `failed` and deliver a failure notice at the Normal tier (agent-less, so kept programmatic) |
| R12 | Background completion and failure additionally emit a structured status payload on the `tasks:instance-finished` app event (source, instance ID, status, message) for cross-extension consumers (this is the tasks extension's own event — distinct from the `notify` event owned by notifications/detached-processes) |
| R12a | On background completion the persistent session is disposed and the registered post-processors run once via `app.sessions.runPostProcessors` (phase order, error-isolated) with `transcriptPath` set to the session's pi JSONL file, so workspace-state processors (git commit/push, project state) AND transcript-dependent processors (episodic memory, core-context) both run against the real transcript. A legacy instance with no session file (predating persistent sessions) is the one case where transcript-dependent processors no-op |
| R13 | A background run pauses for user input by calling the `ask_user` tool: the instance transitions to `waiting`, the persistent session file is persisted (alongside the question and a progress excerpt for the legacy fallback), the session is disposed, the question is queued at the Urgent `tier` (with the instance ID), a `waiting` status payload is emitted on the `tasks:instance-finished` event, and the run returns — releasing its concurrency slot |
| R13a | `respond_to_task` (registered in the main conversation) relays the user's reply to a `waiting` instance: it stores the trimmed response, leaving the instance `waiting` for the background dispatch pass to resume; it errors on an empty response, an unknown instance, an instance that is not `waiting`, or one that already has a pending response |
| R13b | The background dispatch pass resumes a `waiting` instance once it has a stored response: when a persistent session file is recorded the run reopens that session (`SessionManager.open`) and prompts only the user's reply (the session retains its own history); a legacy instance with no session file falls back to a fresh session prompted with a resume prompt replaying the persisted progress, the question, and the reply; either way the evaluator loop continues and resume bookkeeping (`question`, `resumeContext`) is cleared when the run finishes |
| R13c | A `waiting` instance whose last update is older than `waitTimeoutSeconds` (default 7200) — i.e. one whose question is never answered — is marked `failed` and a failure notice is delivered |
| R14 | Agent-facing tools: `create_task`, `update_task` (partial updates; `enabled=false` archives instead of deleting; a schedule change resets `lastFiredAt`), `list_tasks` (active by default, `archived=true` for disabled), `query_task_instances` (filter by status, type, definition), `respond_to_task` (relay a user reply to a waiting instance) |
| R15 | A definition whose schedule cannot be evaluated is skipped with an error log; the generation pass continues with the remaining definitions |
| R16 | In-flight background executions are tracked across ticks so a slow run is never dispatched twice |
| R17 | Concurrent background executions are capped at `backgroundMaxConcurrent` (default 3); when the cap is reached the dispatcher stops for the tick and the remaining pending instances are picked up on a later tick as slots free |
| R18 | `get_task` fetches a single definition by ID or exact name (ID tried first), returning its full prompt, schedule, status, and a summary of its most recent instance; an unknown reference fails with a clear error |
| R19 | `delete_task` permanently removes a definition by ID or exact name (ID tried first); an unknown reference fails with a clear error |
| R20 | `run_task_now` queues a pending instance scheduled for now (dispatched on the next tick) in one of two mutually-exclusive modes: by-reference (`task` = ID or name) snapshots the definition's prompt and type without mutating the definition, so even an auto-disabled one-shot runs; ad-hoc (`prompt`, optional `type` defaulting to `background`, optional `name`) fires a one-off instance with no parent definition. Providing both or neither, or `name` in by-reference mode, fails with a clear error |
| R21 | Stuck-running sweep: a `running` instance whose start (`startedAt`, falling back to `updatedAt` when never stamped) is older than `runningTimeoutSeconds` (default 1800) — its executor presumed dead or wedged — is marked `failed` with a timeout reason, freeing its concurrency slot, and a failure notice plus a structured status payload are delivered; fresher running instances and non-running instances are untouched |
| R22 | One-shot retention cleanup: a separate scheduled pass (hourly) prunes auto-disabled one-shot definitions and their instances once aged past `oneShotRetentionSeconds` (default 172800, i.e. 48 h). A definition is eligible only when it has fired (`lastFiredAt` set, `enabled = false`) and every instance is terminal (`completed`/`failed`); the retention anchor is the latest instance `completedAt`, falling back to `lastFiredAt` when it produced no instances. A still-pending/running/waiting one-shot, or one within the window, is kept |

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

Pending session instances are injected into the conversation as agent turns through the channel delivery queue (queue semantics in [conversation-loop.md](conversation-loop.md)).

**Acceptance Criteria**:
- Given a pending session instance, when the delivery pass runs, then it is first transitioned to `running` (with `startedAt` stamped), `app.channels.deliver` is called with the rendered task text (a "Scheduled task" label plus the prompt), `tier: "normal"`, and `metadata { kind: "session_task", instanceId }`, and on a successful handoff the instance is marked `completed`; the coordinator injects the queued delivery as a fresh turn the agent acts on
- Given the delivery handoff throws, then the instance is rolled back to `pending` with `startedAt` cleared and is retried on the next tick (the transient `running` state means an interrupted delivery could in principle be caught by the stuck-running sweep, though delivery is synchronous in practice)
- Given pending background instances, when the session delivery pass runs, then they are not delivered

### Background Execution (R9, R9a, R9b, R10, R11, R12, R12a, R16, R17)

Background instances execute autonomously: a single persistent pi session is opened once and prompted once per iteration, then a classifier decides whether the workflow is finished.

**Acceptance Criteria**:
- Given a pending background instance, when the runner dispatches it, then it is marked `running`, a persistent pi session is opened (its file recorded on the instance immediately so a mid-run crash still records the resumable path), and the agent runs with a background system prompt that includes the current date and time in the configured timezone, the `ask_user` custom tool, the built-in filesystem/bash tools, and the curated background extension tools (skills + `delegate_to_agent`, git, projects, detached-processes, notifications including `notify_user`, task tools) — with no hard allowlist filtering them out
- Given the background-scoped context sections (memory indexes, projects, subsystem usage), when the run starts, then each is injected once via pi's `before_agent_start`; the opening prompt is just the task, and later iterations need no re-injection because the persistent session retains its history
- Given the evaluator returns `continue`, then the same session is prompted again with a short nudge carrying only the evaluator's observation — no excerpt replay of the previous response
- Given the evaluator returns `complete`, then the instance is marked `completed`, the session is disposed, the registered post-processors run once with the session's transcript path (git/projects persistence AND episodic/core-context memory extraction), and a `completed` status payload is emitted on the `tasks:instance-finished` event — no programmatic result notice is delivered (the agent surfaces results via `notify_user` at its discretion)
- Given the evaluator returns `error`, the iteration cap is reached, or the run throws, then the instance is marked `failed` and a Normal-tier failure notice is delivered alongside a `failed` status payload on the `tasks:instance-finished` event
- Given a run spans multiple ticks, then the runner does not dispatch the same instance twice
- Given more pending background instances than `backgroundMaxConcurrent`, when the tick dispatches, then at most `backgroundMaxConcurrent` run at once and the surplus stay pending until a slot frees on a later tick

### Interactive Await / Respond (R7, R13, R13a, R13b)

A background run that genuinely cannot proceed without user input calls the `ask_user` tool to pose a blocking question. The instance suspends into `waiting`, the question reaches the user, and a later `respond_to_task` reply resumes the run from where it paused. The persistent session file is recorded so the resumed run reopens the same pi session and continues with full continuity; the progress excerpt is still persisted so legacy instances (no session file) can fall back to excerpt replay.

**Acceptance Criteria**:
- Given a background run calls `ask_user`, when the run returns, then the instance is marked `waiting` with the persistent session file persisted (alongside the question and a progress excerpt), `userResponse` cleared, the session disposed, the evaluator not consulted, the question queued at the Urgent `tier` (text includes the instance ID), and a `waiting` status payload emitted on the `tasks:instance-finished` event — the run returns so its concurrency slot frees
- Given a `waiting` instance, when the agent calls `respond_to_task` with the instance ID and a non-empty reply, then the trimmed reply is stored as `userResponse` and the instance stays `waiting` for the dispatch pass to resume; an empty reply, an unknown instance, a non-`waiting` instance, or one that already has a pending response each throws a clear error
- Given a `waiting` instance with a stored `userResponse` and a recorded session file, when the background dispatch pass runs, then it is resumed ahead of fresh pending instances: the run reopens that pi session and is prompted only the user's reply, the evaluator loop continues, and on completion `question`/`resumeContext` are cleared
- Given a `waiting` instance with a stored `userResponse` but no session file (legacy), when the dispatch pass runs, then it resumes in a fresh session prompted with a resume prompt replaying `resumeContext`, the question, and the reply
- Given a `waiting` instance with no stored `userResponse`, when the dispatch pass runs, then it is left untouched

### Expiration, Maintenance Sweeps, and Crash Recovery (R7, R13c, R21, R22)

**Acceptance Criteria**:
- Given a `waiting` instance whose `updatedAt` is older than the wait timeout (its question was never answered), when the expiration pass runs, then it is marked `failed` with a timeout reason and a failure notice is delivered; fresher waiting instances and non-waiting instances are untouched
- Given a `running` instance whose `startedAt` (or `updatedAt` when never stamped) is older than `runningTimeoutSeconds`, when the stuck-running sweep runs, then it is marked `failed` with a "exceeded running timeout" reason, a failure notice and a structured status payload are delivered, and its concurrency slot is freed; a fresh running instance within the timeout and non-running instances are untouched
- Given an auto-disabled one-shot definition past `oneShotRetentionSeconds` whose every instance is terminal, when the cleanup pass runs, then the definition and its instances are deleted; a recently fired one-shot, one whose latest completion is within the window, or one with a non-terminal instance is kept
- Given instances were left `running` when the process died, when the crash-recovery bootstrap hook runs at startup, then they are marked `failed` with a restart reason

### Task Tools (R14, R18, R19, R20)

The task management tools are registered into every conversational agent session via `app.agent.use` (DES-001).

**Acceptance Criteria**:
- Given the agent calls `update_task` with a new schedule, then `lastFiredAt` resets to null so the new schedule is treated as fresh; updates without a schedule leave it untouched
- Given the agent calls `list_tasks`, then enabled definitions are listed with ID, name, type, schedule, and last-fired time (formatted in the configured timezone); `archived=true` lists disabled definitions instead
- Given the agent calls `query_task_instances` with filters, then matching instances are listed newest-first with status, schedule time, parent definition, and result
- Given the agent calls `get_task` with an ID or exact name, then the full definition is returned (name, ID, type, status, schedule, last-run, created time, full prompt) plus a summary of its most recent instance when one exists; an unknown reference throws `Task '<ref>' not found.`
- Given the agent calls `delete_task` with an ID or exact name, then the matching definition is removed and a confirmation naming the task is returned; an unknown reference throws `Task '<ref>' not found.`
- Given the agent calls `run_task_now` with `task` (ID or name), then a pending instance is created with the definition's prompt, type, and `scheduledFor = now`, leaving the definition's `enabled`, `schedule`, and `lastFiredAt` unchanged; the next tick dispatches it through the normal session-delivery or background path
- Given the agent calls `run_task_now` with `prompt` (and optional `type`/`name`), then a pending instance with no parent definition is created (type defaults to `background`); providing both `task` and `prompt`, neither, or `name` alongside `task` throws a clear error
- Given the agent calls `respond_to_task` with a waiting instance's ID and the user's reply, then the reply is stored on the instance for the background dispatch pass to resume; the tool guards against empty replies and against responding to an unknown, non-`waiting`, or already-answered instance
