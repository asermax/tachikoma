# Tasks

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The tasks extension gives Tachikoma scheduled work. Persistent task definitions describe what the agent should do and when — a recurring cron expression or a one-shot ISO datetime — and task instances represent individual firings generated from those definitions. Session instances are queued into the conversation as proactive agent turns (Normal tier); background instances run autonomously through a goal-driven self-declaration loop on a single persistent pi session that spans every iteration and survives an `ask_user` pause — the agent works an explicit `goal` and declares its own terminal outcome (`completed` or `not completable`) via the `update_goal` tool, with the iteration cap as the sole safety net. The extension also contributes a usage context section (scope: main + background) describing the task model, schedule formats, and goal authoring, with run limits and the scheduled-task turn shape in its reference file (`references/tasks.md`, per [DES-014](../design/DES-014-two-tier-agent-facing-documentation.md)).

The agent manages definitions conversationally through registered tools (`create_task`, `update_task`, `get_task`, `delete_task`, `list_tasks`, `run_task_now`, `query_task_instances`, `stop_task`). All scheduling state lives in SQLite, so definitions and pending instances survive restarts.

## User Stories

- As a user, I want to schedule one-shot or recurring work conversationally so that Tachikoma reminds me and follows up without me triggering every action
- As a user, I want background work to run autonomously and to be told the outcome so that long tasks don't block our conversation
- As a user, I want background tasks to work toward a defined goal and decide for themselves when it is met — backing the claim with evidence — so long autonomous runs complete reliably, and a task that genuinely cannot be finished tells me why instead of looping to the cap
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
| R7 | Instance lifecycle: `pending → running → (waiting → running)* → completed \| failed`. A background run can suspend mid-execution into `waiting` via the `ask_user` tool and resume back to `running` once answered; a crash-recovery bootstrap hook marks instances left `running` by a previous process as `failed`; the `stop_task` tool is an additional terminal transition — it cancels any non-terminal instance to `failed` (see R23) |
| R8 | Pending session instances are transitioned to `running` (with `startedAt` stamped), then handed to channel delivery at the Normal `tier`; a successful handoff marks the instance `completed`, while a failed handoff rolls it back to `pending` (clearing `startedAt`) for retry on the next tick |
| R9 | Pending background instances run through a goal-driven self-declaration loop on a single persistent pi session opened once per execution, capped at `backgroundMaxIterations` (default 10); the session is prompted repeatedly across iterations (retaining its own history) and is disposed on every exit path; a background run uses the main conversational session's tool model (no hard tool allowlist) so its bound extension tools stay active alongside the built-in filesystem/bash tools, the `ask_user` and `update_goal` custom tools, and the notifications extension's background-scoped `notify_user` |
| R9a | A background run binds a curated subset of extension factories — those opted in via `app.agent.use(factory, { background: true })`: skills (workspace skill sources + `delegate_to_agent`, including the built-in general-purpose subagent), git, projects, detached-processes, notifications, and the task tools — so an autonomous task has the capabilities it needs; interactive/channel-only or risky surfaces (telegram, self-update, external) are deliberately not bound |
| R9b | A background run receives workspace context (memory indexes, projects, subsystem usage) through the background-scoped extension context sections it binds — each injected once via pi's `before_agent_start` — not folded into the task prompt; the opening prompt is just the task itself |
| R10 | A background run drives its own completion and continuation: after each turn the loop reads the agent's intent — there is no post-turn evaluator, evaluation schema, or fail-open path. An `ask_user` call pauses the run (R13); a valid `update_goal` call terminates it (R27); a turn that does neither continues with a completion nudge (R28) |
| R11 | Background completion does **not** emit a programmatic notice — the agent decides whether to surface results by calling `notify_user` (per the task's own prompt guidance); the instance result is still stored. Failures — a `not completable` declaration, the iteration cap, or a thrown error — mark the instance `failed` and emit a `warning`-severity failure notice on the `notify` event (agent-less, so kept programmatic; the notifications router maps `warning` → the Normal delivery tier). The iteration cap is the sole automatic fail-point for a run that never declares — a single, identifiable failure call a future escalation can intercept |
| R12 | Background-task notices flow through the notifications pipeline: failures, the stuck/expired sweeps, and the `ask_user` pause emit a `NotifyPayload` (`text`, `severity`, `source`) on the shared `notify` app event so they share the router's batching, dedup, severity→tier mapping, and idle gating with every other producer. The tasks extension carries no notice event of its own |
| R12a | On background completion the persistent session is disposed and the registered post-processors run once via `app.sessions.runPostProcessors` (phase order, error-isolated) with `transcriptPath` set to the session's pi JSONL file, so workspace-state processors (git commit/push, project state) AND transcript-dependent processors (episodic memory, core-context) both run against the real transcript. A legacy instance with no session file (predating persistent sessions) is the one case where transcript-dependent processors no-op |
| R13 | A background run pauses for user input by calling the `ask_user` tool: the instance transitions to `waiting`, the persistent session file is persisted (alongside the question and a progress excerpt for the legacy fallback), the session is disposed, the question is emitted as an `urgent`-severity notice on the `notify` event (text includes the instance ID; the router queues urgent notices at the Urgent tier), and the run returns — releasing its concurrency slot |
| R13a | `respond_to_task` (registered in the main conversation) relays the user's reply to a `waiting` instance: it stores the trimmed response, leaving the instance `waiting` for the background dispatch pass to resume; it errors on an empty response, an unknown instance, an instance that is not `waiting`, or one that already has a pending response |
| R13b | The background dispatch pass resumes a `waiting` instance once it has a stored response: when a persistent session file is recorded the run reopens that session (`SessionManager.open`) and prompts only the user's reply (the session retains its own history); a legacy instance with no session file falls back to a fresh session prompted with a resume prompt replaying the persisted progress, the question, and the reply; either way the self-declaration loop continues and resume bookkeeping (`question`, `resumeContext`) is cleared when the run finishes |
| R13c | A `waiting` instance whose last update is older than `waitTimeoutSeconds` (default 7200) — i.e. one whose question is never answered — is marked `failed` and a `warning`-severity failure notice is emitted on the `notify` event |
| R14 | Agent-facing tools: `create_task`, `update_task` (partial updates; `enabled=false` archives instead of deleting; a schedule change resets `lastFiredAt`), `list_tasks` (active by default, `archived=true` for disabled), `query_task_instances` (filter by status, type, definition), `respond_to_task` (relay a user reply to a waiting instance) |
| R15 | A definition whose schedule cannot be evaluated is skipped with an error log; the generation pass continues with the remaining definitions |
| R16 | In-flight background executions are tracked across ticks so a slow run is never dispatched twice |
| R17 | Concurrent background executions are capped at `backgroundMaxConcurrent` (default 3); when the cap is reached the dispatcher stops for the tick and the remaining pending instances are picked up on a later tick as slots free |
| R18 | `get_task` fetches a single definition by ID or exact name (ID tried first), returning its goal (the full text, or an explicit not-set marker when none), prompt, schedule, status, the `since` schedule-anchor timestamp, and a summary of its most recent instance; an unknown reference fails with a clear error |
| R19 | `delete_task` permanently removes a definition by ID or exact name (ID tried first); an unknown reference fails with a clear error |
| R20 | `run_task_now` queues a pending instance scheduled for now (dispatched on the next tick) in one of two mutually-exclusive modes: by-reference (`task` = ID or name) snapshots the definition's prompt and type without mutating the definition, so even an auto-disabled one-shot runs; ad-hoc (`prompt`, optional `type` defaulting to `background`, optional `name`) fires a one-off instance with no parent definition. Providing both or neither, or `name` in by-reference mode, fails with a clear error |
| R21 | Stuck-running sweep: a `running` instance whose start (`startedAt`, falling back to `updatedAt` when never stamped) is older than `runningTimeoutSeconds` (default 1800) — its executor presumed dead or wedged — is marked `failed` with a timeout reason, freeing its concurrency slot, and a `warning`-severity failure notice is emitted on the `notify` event; fresher running instances and non-running instances are untouched |
| R22 | One-shot retention cleanup: a separate scheduled pass (hourly) prunes auto-disabled one-shot definitions and their instances once aged past `oneShotRetentionSeconds` (default 172800, i.e. 48 h). A definition is eligible only when it has fired (`lastFiredAt` set, `enabled = false`) and every instance is terminal (`completed`/`failed`); the retention anchor is the latest instance `completedAt`, falling back to `lastFiredAt` when it produced no instances. A still-pending/running/waiting one-shot, or one within the window, is kept |
| R23 | `stop_task` cancels a task instance by ID from any non-terminal state (`pending`/`running`/`waiting`) to `failed` with a "Task cancelled by user" result — pending instances never run and waiting ones never resume. A `running` instance additionally has its live self-declaration loop aborted: an `AbortSignal` whose listener calls pi `session.abort()` ends a mid-flight turn promptly, and the executor's abort path writes no status (the cancel initiator owns the terminal write, so there is no race). Terminal (`completed`/`failed`) and unknown instances fail with a clear error. No notice is emitted — the tool result is the confirmation. Registered in the operational task factory (bound to `main` + `background`) |

| R24 | Task definitions and instances carry an optional free-text `goal` — what "done" means for the run, in the agent's own terms. An instance's `goal` is **snapshotted** from its definition at creation (or carried inline for an ad-hoc run); a definition `goal` written back by extraction *after* an instance was created never reaches that instance — it extracts its own goal at run start. A null goal means "not yet provided"; the field is never backfilled — existing definitions acquire a goal lazily on their first run (R26) |
| R25 | `goal` is an optional parameter on `create_task`, `update_task`, and `run_task_now`: create/update persist it on the definition; a by-reference `run_task_now` snapshots the definition goal, an explicit `run_task_now` `goal` overrides it for that run only, and an ad-hoc `run_task_now` carries the goal inline (null when omitted) |
| R26 | When a background run starts with no goal, the executor derives one from the task prompt via a tool-enabled extraction run (`side.run`, granted the read-only filesystem tools so it can read files the prompt references and the derived goal reflects the task's full scope) and stores it on the instance; if a usable goal is extracted for an instance whose parent definition still has no goal, it is written back to the definition **only when its goal is still null** (never clobbering one set by `update_task` or an earlier write-back) so later runs reuse it without re-extracting. On extraction failure or an unusable result (empty, trivial, or below a minimum-length heuristic) the run proceeds on the task-prompt basis, persists nothing, and every later run retries extraction until it produces a usable, persisted goal — there is no backfill pass and no give-up counter |
| R27 | A single per-run `update_goal` tool is the sole way the agent declares a terminal outcome. A valid `completed` declaration restates the goal, cites concrete checkable evidence that its stated check is met, and summarizes what was accomplished (validated in-tool, so an incomplete declaration is rejected and retried within the same run); it marks the instance `completed` with the summary as its `result`, disposes the session, and runs the post-processors once — no notice is emitted. A `not completable` declaration (with a reason) marks the instance `failed` with the reason as its `result` and emits a `warning`-severity notice carrying the reason. The re-stated goal and cited evidence form the audit trail in the session transcript |
| R28 | A turn that ends without a terminal declaration and without `ask_user` triggers a per-turn completion nudge — a user message asking the agent to evaluate the goal and call `update_goal` if its check is met, while explicitly allowing it to keep working; it is re-injected each non-terminal turn but never forces a premature declaration |
| R29 | Extensions can request an ad-hoc background run programmatically: emitting a `task:dispatch-background` app event (payload `{prompt, goal?, source}`) has the tasks extension create a pending background instance with no parent definition (`definitionId: null`), dispatched by the existing tick like any ad-hoc `run_task_now` instance. A missing/blank `goal` is normalized to `null` (the runner can extract one); a payload with no usable `prompt` is logged and dropped — never thrown into the emitter. Producers must not rely on dispatch: with the tasks extension disabled the event has no subscriber and the request is silently dropped |

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

### Background Execution (R9, R9a, R9b, R10, R11, R12, R12a, R16, R17, R27, R28)

Background instances execute autonomously: a single persistent pi session is opened once and prompted once per iteration, then the agent itself decides whether the workflow is finished via `update_goal`.

**Acceptance Criteria**:
- Given a pending background instance, when the runner dispatches it, then it is marked `running`, a persistent pi session is opened (its file recorded on the instance immediately so a mid-run crash still records the resumable path), and the agent runs with a background system prompt that includes the current date and time in the configured timezone, the `ask_user` custom tool, the built-in filesystem/bash tools, and the curated background extension tools (skills + `delegate_to_agent`, git, projects, detached-processes, notifications including `notify_user`, task tools) — with no hard allowlist filtering them out
- Given the background-scoped context sections (memory indexes, projects, subsystem usage), when the run starts, then each is injected once via pi's `before_agent_start`; the opening prompt is just the task, and later iterations need no re-injection because the persistent session retains its history
- Given a turn ends with no terminal declaration and no `ask_user` call, then the same session is prompted again with the completion nudge (a user message re-evaluating the goal) — no excerpt replay of the previous response
- Given the agent declares `completed` via `update_goal` (restated goal + evidence + summary, all validated in-tool), then the instance is marked `completed` with the summary as its `result`, the session is disposed, the registered post-processors run once with the session's transcript path (git/projects persistence AND episodic/core-context memory extraction), and no notice is emitted — the agent surfaces results via `notify_user` at its discretion
- Given the agent declares `not completable`, the iteration cap is reached without a declaration, or the run throws, then the instance is marked `failed` and a `warning`-severity failure notice is emitted on the `notify` event (the router maps `warning` → Normal tier)
- Given a run spans multiple ticks, then the runner does not dispatch the same instance twice
- Given more pending background instances than `backgroundMaxConcurrent`, when the tick dispatches, then at most `backgroundMaxConcurrent` run at once and the surplus stay pending until a slot frees on a later tick

### Goal-driven completion (R24, R25, R26, R27, R28)

A background run works an explicit goal and declares its own terminal outcome; the goal is derived and persisted lazily when not provided.

**Acceptance Criteria**:
- Given the agent calls `create_task` or `update_task` with a `goal`, then the goal is persisted on the definition and every subsequently generated instance snapshots it at creation
- Given the agent calls `run_task_now` by reference, then the instance snapshots the definition's current goal; given an explicit `goal`, then it overrides the definition goal for that run only; given ad-hoc `run_task_now` with a `goal`, then the instance carries it directly (null when omitted, so a later run derives one)
- Given a definition created before this capability, then its goal is null until its next run — no backfill pass runs
- Given a fresh background run with a null goal, when the run starts, then the executor derives a goal from the task prompt via a tool-enabled extraction run that can read files the prompt references, stores it on the instance, and (when a parent definition still has no goal) writes it back so the next run reuses it without re-extracting
- Given the definition's goal was set between the instance's creation and its run-start extraction, when write-back is attempted, then it is skipped — the existing definition goal is never clobbered
- Given an ad-hoc instance (no parent definition) with no goal, when extraction succeeds, then the goal is stored on the instance only
- Given extraction throws or returns an unusable goal (empty, trivial, or below a minimum-length heuristic), when the run starts, then the run proceeds on the task-prompt basis, nothing is persisted, and the next run retries extraction — there is no give-up counter
- Given the agent declares `completed` via `update_goal` with a restated goal, concrete evidence, and a summary (all validated in-tool), then the instance is marked `completed` with the summary as its `result`, the session is disposed, and the post-processors run once — no notice is emitted
- Given an incomplete `completed` declaration (a missing required field), then the tool signals an error surfaced to the agent for in-run self-correction, the declaration flag stays unset, the run continues, and a later valid call completes it
- Given the agent declares `not completable` with a reason, then the instance is marked `failed` with the reason as its `result` and a `warning`-severity notice carrying the reason is emitted on the `notify` event
- Given the agent never declares a terminal outcome (and never pauses via `ask_user`), when the run reaches the iteration cap, then the instance is marked `failed` with a max-iterations reason and a `warning`-severity notice is emitted — the sole automatic failure path

### Interactive Await / Respond (R7, R13, R13a, R13b)

A background run that genuinely cannot proceed without user input calls the `ask_user` tool to pose a blocking question. The instance suspends into `waiting`, the question reaches the user, and a later `respond_to_task` reply resumes the run from where it paused. The persistent session file is recorded so the resumed run reopens the same pi session and continues with full continuity; the progress excerpt is still persisted so legacy instances (no session file) can fall back to excerpt replay.

**Acceptance Criteria**:
- Given a background run calls `ask_user`, when the run returns, then the instance is marked `waiting` with the persistent session file persisted (alongside the question and a progress excerpt), `userResponse` cleared, the session disposed, the run returned without further turns, and the question emitted as an `urgent`-severity notice on the `notify` event (text includes the instance ID; the router queues urgent at the Urgent tier) — the run returns so its concurrency slot frees
- Given a `waiting` instance, when the agent calls `respond_to_task` with the instance ID and a non-empty reply, then the trimmed reply is stored as `userResponse` and the instance stays `waiting` for the dispatch pass to resume; an empty reply, an unknown instance, a non-`waiting` instance, or one that already has a pending response each throws a clear error
- Given a `waiting` instance with a stored `userResponse` and a recorded session file, when the background dispatch pass runs, then it is resumed ahead of fresh pending instances: the run reopens that pi session and is prompted only the user's reply, the self-declaration loop continues, and on completion `question`/`resumeContext` are cleared
- Given a `waiting` instance with a stored `userResponse` but no session file (legacy), when the dispatch pass runs, then it resumes in a fresh session prompted with a resume prompt replaying `resumeContext`, the question, and the reply
- Given a `waiting` instance with no stored `userResponse`, when the dispatch pass runs, then it is left untouched

### Expiration, Maintenance Sweeps, and Crash Recovery (R7, R13c, R21, R22)

**Acceptance Criteria**:
- Given a `waiting` instance whose `updatedAt` is older than the wait timeout (its question was never answered), when the expiration pass runs, then it is marked `failed` with a timeout reason and a `warning`-severity failure notice is emitted on the `notify` event; fresher waiting instances and non-waiting instances are untouched
- Given a `running` instance whose `startedAt` (or `updatedAt` when never stamped) is older than `runningTimeoutSeconds`, when the stuck-running sweep runs, then it is marked `failed` with a "exceeded running timeout" reason, a `warning`-severity failure notice is emitted on the `notify` event, and its concurrency slot is freed; a fresh running instance within the timeout and non-running instances are untouched
- Given an auto-disabled one-shot definition past `oneShotRetentionSeconds` whose every instance is terminal, when the cleanup pass runs, then the definition and its instances are deleted; a recently fired one-shot, one whose latest completion is within the window, or one with a non-terminal instance is kept
- Given instances were left `running` when the process died, when the crash-recovery bootstrap hook runs at startup, then they are marked `failed` with a restart reason

### Task Tools (R14, R18, R19, R20, R23)

The task management tools are registered into every conversational agent session via `app.agent.use` (DES-001).

**Acceptance Criteria**:
- Given the agent calls `update_task` with a new schedule, then `lastFiredAt` resets to null so the new schedule is treated as fresh; updates without a schedule leave it untouched
- Given the agent calls `list_tasks`, then enabled definitions are listed with ID, name, type, schedule, last-fired time (formatted in the configured timezone), and a one-line goal summary (the goal's first line, capped at 100 chars) for tasks that have a goal; `archived=true` lists disabled definitions instead
- Given the agent calls `query_task_instances` with filters, then matching instances are listed newest-first with status, schedule time, parent definition, and result
- Given the agent calls `get_task` with an ID or exact name, then the full definition is returned (name, ID, type, status, schedule, last-run, created time, the `since` schedule-anchor time, the goal in full or an explicit not-set marker, and the full prompt) plus a summary of its most recent instance when one exists; an unknown reference throws `Task '<ref>' not found.`
- Given the agent calls `delete_task` with an ID or exact name, then the matching definition is removed and a confirmation naming the task is returned; an unknown reference throws `Task '<ref>' not found.`
- Given the agent calls `run_task_now` with `task` (ID or name), then a pending instance is created with the definition's prompt, type, and `scheduledFor = now`, leaving the definition's `enabled`, `schedule`, and `lastFiredAt` unchanged; the next tick dispatches it through the normal session-delivery or background path
- Given the agent calls `run_task_now` with `prompt` (and optional `type`/`name`), then a pending instance with no parent definition is created (type defaults to `background`); providing both `task` and `prompt`, neither, or `name` alongside `task` throws a clear error
- Given the agent calls `respond_to_task` with a waiting instance's ID and the user's reply, then the reply is stored on the instance for the background dispatch pass to resume; the tool guards against empty replies and against responding to an unknown, non-`waiting`, or already-answered instance
- Given the agent calls `stop_task` with a `pending` or `waiting` instance's ID, then the instance is marked `failed` with a "Task cancelled by user" result (`completedAt` set), is never dispatched or resumed, and the tool returns a confirmation naming the ID and prior status
- Given the agent calls `stop_task` with a `running` instance's ID, then the instance is marked `failed`, its live pi session is aborted so the in-flight turn ends, and the executor does not overwrite the cancelled status or emit a failure notice for it
- Given the agent calls `stop_task` with a terminal (`completed`/`failed`) or unknown instance, then the tool throws a clear error and the DB row is unchanged

### Programmatic Background Dispatch (R29)

Extensions can request an ad-hoc background run by emitting the `task:dispatch-background` app event; the tasks extension is the subscriber.

**Acceptance Criteria**:
- Given a well-formed `task:dispatch-background` payload (non-blank `prompt`), when the event fires, then a pending background instance is created (`definitionId: null`, `goal` set or `null`, `scheduledFor: now`) and the existing tick dispatches it like any ad-hoc instance
- Given a payload whose `goal` is absent or blank, when parsed, then the instance's goal is `null` (the runner's goal extraction applies later)
- Given a payload with no usable `prompt`, when parsed, then the payload is logged and dropped — no instance is created and the emitter is unaffected
- Given the tasks extension is disabled, when a producer emits the event, then the request is silently dropped (no subscriber) — the [skill-evolution](skill-evolution.md) reporter is the current producer
