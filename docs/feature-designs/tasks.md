# Design: Tasks

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/tasks.md](../feature-specs/tasks.md)
**Status**: Current

## Purpose

This document explains how scheduled tasks are implemented on the unified extension API: the single-tick orchestration, the definition/instance split, idle-gated session delivery, and the background evaluator loop over a single persistent pi session per instance.

## Problem Context

pi deliberately provides no scheduling, idle gating, or buffered delivery — those are host concerns ([pi-sdk-notes](../reference/pi-sdk-notes.md)). The tasks extension must express that capability using only the AppContext services an extension is given (DES-001): `app.scheduler`, `app.channels.deliver`, `app.agent.side`, `app.db`, `app.events`.

**Constraints:**
- Extensions cannot reach coordinator internals — idle gating is owned by the conversation loop and reachable only through `app.channels.deliver` with `gate: "idle"` (see [conversation-loop.md](../feature-specs/conversation-loop.md))
- Ticks must be cheap and idempotent: the scheduler's overlap protection skips a tick if the previous one is still running, so passes cannot assume exactly-once timing
- Background runs must not block the tick — a slow agent run can span many ticks
- Tests fake `SideRunner` and the delivery function with `Pick<>` types (DES-002), so all logic modules take their dependencies as narrow parameters

**Interactions:**
- Channel delivery (`conversation-loop.md` / [telegram.md](../feature-specs/telegram.md)): all user-facing output goes through `app.channels.deliver`
- Notifications extension: task status payloads ride the tasks extension's own `tasks:instance-finished` app event (not the `notify` event, which notifications/detached-processes own) and are deliberately not user notifications; user-facing output goes through `app.channels.deliver` (see [notifications.md](notifications.md))
- Agent manager: `app.agent.side.openBackgroundSession` (a persistent pi session bound with the background factories + custom tools, prompted repeatedly across the loop) and `app.agent.side.classify` (structured classification) power background execution

## Design Overview

`src/extensions/tasks/index.ts` wires one `tasks-tick` job (`app.scheduler.every`, 60 s) that runs five pure passes in order: `generateDueInstances` (definitions → pending instances), `expireWaitingInstances` (waiting timeout sweep), `failStuckRunningInstances` (running timeout sweep), `deliverSessionTasks` (pending session instances → idle-gated channel delivery), and `BackgroundRunner.tick()` (pending background instances → detached evaluator-loop executions). A second, slower `tasks-one-shot-cleanup` job (`app.scheduler.every`, 3600 s) runs `cleanupExpiredOneShots` (retention pruning). Each pass is a standalone module taking its dependencies (`repository`, `deliver`, `side`, `now`, `log`) as parameters; `index.ts` contains no logic beyond wiring and the crash-recovery bootstrap hook.

```
tasks-tick (60s)
  ├─ generation.ts    definitions ──→ pending instances
  ├─ expiration.ts    waiting > timeout (unanswered) ──→ failed (+ notice)
  ├─ stuck-running.ts running > runningTimeout ──→ failed (+ notice, frees slot)
  ├─ session-delivery.ts  pending session ──→ channels.deliver(idle) ──→ completed
  └─ executor.ts      answered waiting + pending background ──→ side.run ⇄ side.classify loop (detached)
                        ask_user tool ──→ waiting (+ question delivered)

tasks-one-shot-cleanup (3600s)
  └─ one-shot-cleanup.ts  aged auto-disabled one-shots (all instances terminal) ──→ deleted
```

A background run can pause for input: the in-run `ask_user` tool flips the instance to `waiting`, persists the question and a progress excerpt, surfaces the question to the user, and the run returns (freeing its slot). The main-session `respond_to_task` tool stores the user's reply; the next background dispatch pass resumes the instance ahead of fresh pending work, replaying the persisted progress + question + reply into a resume prompt. An unanswered `waiting` instance is failed by the expiration sweep — the producer that makes that sweep live.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/tasks/index.ts` | Wiring: config, crash-recovery bootstrap, tick orchestration, tool registration | Per-minute `tasks-tick` job plus a slower hourly `tasks-one-shot-cleanup` job, instead of per-concern loops |
| `src/extensions/tasks/schema.ts` | Drizzle tables and type maps | Schedule stored as a JSON discriminated union (`cron` expression / `once` ISO instant); `since` and `updatedAt` columns anchor stale-cron prevention and the waiting sweep; `question` + `resumeContext` columns persist a paused run's blocking question and progress excerpt (legacy-resume fallback), `userResponse` carries the reply that triggers resume; `piSessionFile` (migration `0005_tasks_pi_session_file`) records the persistent pi session backing the run — resumed across iterations and `ask_user` pauses, and fed to memory extraction on completion |
| `src/extensions/tasks/repository.ts` | All SQL access | Stamps `since` (definitions) and `updatedAt` (instances) on every update; period-aware duplicate query keyed on exact `scheduledFor`; `resolveDefinition` (ID then exact name), `deleteDefinition`, and `getLatestInstanceForDefinition` back the get/delete/run-now tools; `getResumableInstances` returns `waiting` instances whose `userResponse` has arrived; `listStuckRunningInstances` filters `running` rows on `startedAt ?? updatedAt`; `pruneExpiredOneShotDefinitions` deletes aged auto-disabled one-shots whose instances are all terminal (anchored on latest `completedAt`, else `lastFiredAt`) |
| `src/extensions/tasks/schedule.ts` | Parse and format schedules | A croner probe distinguishes cron from one-shot input (`getPattern()` is undefined for datetimes); timezone handling delegated entirely to croner |
| `src/extensions/tasks/generation.ts` | One generation pass | Anchor = `lastFiredAt`, else current hour start minus 1 s; advances past `since` instead of skipping the definition |
| `src/extensions/tasks/session-delivery.ts` | One session-delivery pass | Marks the instance `completed` at handoff; rolls back to `pending` if the handoff throws |
| `src/extensions/tasks/executor.ts` | Background evaluator loop + `BackgroundRunner` dispatcher | One persistent `side.openBackgroundSession(...)` opened per execution (resumed from `piSessionFile` when present) — binds the curated background factories with no hard tool allowlist so their tools stay active; the session file is recorded immediately, the opening prompt is just the task (workspace context arrives via the background-scoped extension context sections' `before_agent_start`, not a manual fold), and each continuation is a short nudge carrying only the evaluator observation (the session retains its own history); on completion the session is disposed and `runPostProcessors` runs with the session's transcript path; the in-run `ask_user` custom tool signals a pause the loop turns into a `waiting` transition (persisting `piSessionFile`) before disposing the session; a resumed instance prompts only the user's reply (legacy instances with no session file fall back to a resume-prompt replay); a `try/finally` guarantees disposal on every exit path; in-flight map prevents double dispatch and caps concurrency at `backgroundMaxConcurrent` |
| `src/extensions/tasks/expiration.ts` | Waiting-instance timeout sweep | Threshold on `updatedAt`; `onExpired` callback lets `index.ts` own user notice + event emission; now has a live producer (`ask_user`) |
| `src/extensions/tasks/stuck-running.ts` | Stuck-running timeout sweep | Mirrors `expiration.ts`: fails `running` instances past `runningTimeoutSeconds`, freeing their concurrency slot; `onStuck` callback lets `index.ts` own user notice + event emission |
| `src/extensions/tasks/one-shot-cleanup.ts` | One-shot retention pruning | Thin wrapper over `pruneExpiredOneShotDefinitions`; runs on the slower cleanup job since retention is low-churn |
| `src/extensions/tasks/tools.ts` | Agent-facing tools | Pure handlers over `ToolDeps`; the pi factory only wraps them in `registerTool` calls; `run_task_now` creates a pending instance and returns — dispatch is the existing tick path, not a direct executor call; `respond_to_task` stores a reply on a `waiting` instance and lets the dispatch pass resume it |

## Key Decisions

### One per-minute tick, pure passes; a separate slow cleanup job

**Choice**: A single `app.scheduler.every("tasks-tick", 60, ...)` job runs generation, waiting-expiration, stuck-running sweep, session delivery, and background dispatch sequentially, each as a pure function over injected dependencies. Retention pruning (`cleanupExpiredOneShots`) lives on its own `app.scheduler.every("tasks-one-shot-cleanup", 3600, ...)` job rather than inside the per-minute tick.
**Why**: The core scheduler already provides naming, overlap protection, and error guarding (core-shell R9); reimplementing per-concern async loops would duplicate that. Pure passes with injected `now` are trivially testable against a temp database. The stuck-running sweep belongs in the tick because freeing a wedged concurrency slot is lifecycle-critical and cheap; retention pruning is low-churn and time-coarse, so running it hourly keeps it from scanning definitions every minute and competing with generation/delivery work.
**Alternatives Considered**: Long-lived async loops per concern, coordinated through a shared priority buffer; croner jobs per definition; folding retention pruning into the per-minute tick.
**Consequences**:
- Pro: One place to observe all per-minute task activity; passes share a transactionless, re-entrant style
- Pro: Tests drive passes directly with a fake clock — no timers
- Pro: Retention's coarse cadence keeps the hot tick free of a low-value full-definition scan
- Con: Worst-case latency for any transition is one tick interval (60 s); retention lag is up to one hour
- Con: A pathologically slow pass delays the ones after it within the tick

### Background runs get a curated slice of the agent, not a bare file sandbox

**Choice**: A background run is not a minimal file-tool sandbox — it binds an opted-in subset of extension factories (`app.agent.use(f, { background: true })` → `side.run({ backgroundExtensions: true })`): skills (workspace skill sources + `delegate_to_agent`, including the built-in general-purpose subagent), git, projects, detached-processes, notifications, and the task tools. To keep those tools active it runs with the main session's tool model — **no hard tool allowlist** (a `tools` allowlist would filter extension/custom tools out). The same background-scoped factories also carry each extension's context section (memory/projects/subsystem usage), so workspace context reaches the run through pi's `before_agent_start` automatically; on completion it runs the registered post-processors (`app.sessions.runPostProcessors`) to persist workspace changes.
**Why**: Real scheduled tasks (generate a report from a skill, push a project change) need the same capabilities the interactive agent has, not just read/write/bash — this restores the legacy background agent's reach. Opting factories in per-extension keeps interactive/channel-only and risky surfaces (telegram, self-update, external) out of autonomous runs — and binds exactly the context sections whose tools the run actually has. Reproducing the post-processor pipeline (rather than only the live session getting it) means a background task's workspace edits are committed.
**Alternatives Considered**: Binding every registered factory (pulls in channel/interactive surfaces a headless task should not touch); passing an explicit tool-name allowlist (cannot enumerate dynamic factory tool names, and filters them out).
**Consequences**:
- Pro: Background tasks reach skills, delegation, git, projects, and processes — parity with the interactive agent's relevant surface
- Pro: Per-extension opt-in keeps the autonomous toolset curated and auditable
- Con: No hard allowlist means a background run can use any built-in tool, not a restricted set — acceptable for an autonomous workspace agent, matching the main session
- Pro: Episodic/facts memory of background tasks is now extracted — the persistent session writes a real pi transcript that completion post-processing feeds to memory extraction (see "Persistent pi session per background instance" below)

### Session tasks are injected as agent turns, completed at handoff

**Choice**: `deliverSessionTasks` renders the task prompt into a labelled text block and hands it to `app.channels.deliver` with `gate: "idle"`, `maxHoldSeconds`, and `target: "agent"`; the instance is marked `completed` as soon as the handoff succeeds. The coordinator injects an `agent`-targeted delivery as a fresh turn into the active session (a system-origin `submit` with `boundary: "skip"`), so the agent acts on the prompt and the user sees its response.
**Why**: Channel delivery is the only proactive-output surface DES-001 gives extensions, and its `target` flag lets the coordinator route the rendered prompt into the live session without the extension touching session internals. The conversation loop still owns gating and hold timers, so the pass stays a pure detector/enqueuer.
**Alternatives Considered**: Delivering as plain user-facing text (`target: "user"`) so the prompt is shown verbatim rather than acted on; a dedicated buffer extension that owns idleness itself.
**Consequences**:
- Pro: No duplicated idle logic; delivery ordering and hold behavior are uniform with notifications
- Pro: Rollback-on-throw keeps the instance retryable without a stuck `running` row
- Pro: The agent acts on the task prompt, so session tasks can drive real work rather than only relaying canned text
- Con: "Completed" means "handed to the delivery gate", not "the agent's response reached the user"; a held delivery lost at shutdown is not retried

### Persistent pi session per background instance

**Choice**: `executeBackgroundInstance` opens ONE persistent pi session per execution via `app.agent.side.openBackgroundSession({ system, customTools, sessionFile })` (a thin `SideRunner` method over `AgentManager.open` with `inMemory` off, `bindBackgroundFactories: true`, and no `tools` allowlist), records its `sessionFile` on the instance immediately, then prompts it once per iteration. The opening prompt is just the task (workspace context arrives via the background-scoped context sections); each continuation is a short nudge carrying only the evaluator's observation — the session retains its own tool-call and file-state history, so no excerpt replay is needed. A `try/finally` disposes the session on every exit path.
**Why**: A real pi session is the same primitive the main conversation resumes (`Coordinator.resumeSession` → `AgentManager.open({ sessionFile })`), so background continuity is plumbing, not a pi limitation. Full session continuity preserves tool-call history and intermediate file knowledge across iterations (the old per-iteration ephemeral run replayed only a 4k-char text excerpt and lost everything else), and the session's pi JSONL transcript lets completion post-processing run episodic/facts memory extraction — previously impossible because no transcript existed.
**Alternatives Considered**: The original ephemeral in-memory `side.run` per iteration with prompt-replayed continuity (lost tool/file state and produced no transcript); keeping the session in memory only (no transcript for extraction, no crash-resumable file).
**Consequences**:
- Pro: Full continuity across iterations — tool-call history and file state survive, not just a text excerpt
- Pro: Completion feeds a real transcript to memory extraction, so background work lands in episodic/facts memory
- Pro: The recorded `sessionFile` makes a mid-run crash resumable in principle, and powers `ask_user` resume with full continuity
- Con: A session file is written to the workspace sessions dir per background run — these are not yet swept (see retention gap below)
- Con: `BackgroundSide` widened from `run`/`classify` to `openBackgroundSession`/`classify`; tests now mock a fake persistent session

### Concurrency cap on background dispatch

**Choice**: `BackgroundRunner` holds an in-flight `Map<instanceId, Promise>` and, per tick, dispatches pending background instances only while `inFlight.size < maxConcurrent` (`backgroundMaxConcurrent`, default 3); once the cap is hit it breaks out of the loop and leaves the surplus pending. Freed slots are filled on subsequent ticks (or sooner — the runner re-evaluates on every 60 s tick), and the existing in-flight map still prevents double dispatch of a slow run.
**Why**: Dispatching every pending instance was unbounded — a backlog could spawn dozens of simultaneous side runs, each driving an LLM loop, producing an API-request burst and memory pressure. A simple size check against a configurable ceiling bounds the blast radius without needing a queue or a real semaphore primitive, since the tick already re-polls pending work.
**Alternatives Considered**: A promise-based semaphore that admits queued instances the instant a slot frees (vs. waiting for the next tick). Rejected as overkill — the 60 s tick re-poll is a sufficient and simpler drain mechanism, and instances stay durably `pending` in SQLite rather than held in memory.
**Consequences**:
- Pro: Hard ceiling on concurrent side runs / API burst; surplus work stays durably pending in the DB
- Pro: Reuses the existing in-flight map — no new dedup surface
- Con: A freed slot is only refilled on the next tick, so worst-case dispatch latency for a queued instance is one tick interval

### Interactive await/respond resumes the persistent session (legacy excerpt-replay fallback)

**Choice**: A background run pauses by calling the in-run `ask_user` custom tool. The tool records the question in a closure flag and returns a "stop and wait" message; after the prompt returns, the executor — seeing the flag set — transitions the instance to `waiting`, persisting `piSessionFile` (alongside the question and the run's final text as `resumeContext`), disposes the session, delivers the question to the user (immediate gate, instance ID in the text), emits a `waiting` status payload, then returns without consulting the evaluator. The main-session `respond_to_task` tool stores the trimmed reply as `userResponse`; `BackgroundRunner.tick` dispatches `getResumableInstances` (answered `waiting`) ahead of `getPendingInstances`, and `executeBackgroundInstance` reopens the recorded `piSessionFile` and prompts only the user's reply — the resumed session has full continuity. A legacy instance with no `piSessionFile` (predating persistent sessions) falls back to a fresh session prompted with a resume prompt replaying `resumeContext` + question + reply. Resume bookkeeping is cleared on completion.
**Why**: With a persistent pi session per instance (decision above), the paused run already has a session file on disk — `SessionManager.open(sessionFile)` is exactly how the main conversation resumes, so re-entering the paused agent with full tool/file continuity is the same plumbing rather than a prompt-replay approximation. The legacy fallback keeps instances created before this change safe: their `piSessionFile` is null, so they still resume via the old excerpt replay. Keeping the pause signal in a closure flag (rather than a thrown sentinel) lets the agent's final turn text serve as the legacy progress excerpt for free, and keeps every DB write in the executor where the rest of the lifecycle transitions live.
**Alternatives Considered**: Continuing to resume purely by prompt replay even with a persistent session (discards the available continuity for no gain); routing the reply as a plain inbound message (nothing would re-associate it with the paused instance).
**Consequences**:
- Pro: The resumed run keeps full tool-call history and file state from before the pause — not just a text excerpt
- Pro: The expiration sweep still has a producer, so an abandoned question is bounded by `waitTimeoutSeconds`
- Pro: `respond_to_task` is a pure guarded handler; the resume itself rides the existing dispatch path (concurrency cap, double-dispatch protection for free)
- Pro: Old `waiting` instances remain resumable through the excerpt-replay fallback — no data migration needed
- Con: Resume latency is one tick interval after the reply is stored
- Con: A session file is held on disk across an arbitrarily long user wait (not swept — see retention gap)

### Evaluator is a strict, best-effort classifier

**Choice**: `evaluateCompletion` classifies the agent response as `complete`/`continue`/`error` using an ordered-rules system prompt that explicitly forbids qualitative review; any evaluator exception is mapped to `continue`.
**Why**: A reviewing evaluator caused quality-gating loops (re-doing finished work); ordered factual rules make "announced completion" terminal even when follow-ups are mentioned. Mapping failures to `continue` means a flaky classifier model can never abort or fail a healthy run — the iteration cap is the backstop.
**Consequences**:
- Pro: Deterministic loop termination semantics; cheap classifier-tier model suffices
- Con: A persistently broken evaluator burns all iterations before failing the task

### `run_task_now` queues, the tick dispatches

**Choice**: `run_task_now` only inserts a `pending` instance with `scheduledFor = now` (by-reference snapshots the definition's prompt and type; ad-hoc carries an inline prompt with `definitionId = null` and a defaulted-`background` type). It does not call the executor or session-delivery passes directly — the next `tasks-tick` picks the instance up exactly as it would a generated one. `get_task` and `delete_task` resolve their target through `repository.resolveDefinition`, which tries an exact ID match then an exact name.
**Why**: Reusing the generation→dispatch path means an on-demand run is indistinguishable from a scheduled firing downstream (same lifecycle, concurrency cap, evaluator loop, idle gating), so there is no second dispatch surface to keep in sync. Snapshotting the prompt instead of mutating the definition lets an auto-disabled one-shot be re-run without resurrecting its schedule. ID-or-name resolution keeps the tools usable from conversation where the agent often has the human name, not the UUID.
**Consequences**:
- Pro: One dispatch path; an immediate run inherits the concurrency cap and double-dispatch protection for free
- Pro: By-reference runs never mutate the definition, so schedule/enabled/`lastFiredAt` stay authoritative
- Con: Worst-case latency before an on-demand instance starts is one tick interval (60 s); it is not run synchronously
- Con: Name resolution matches the first exact-name row — duplicate names are resolved arbitrarily (IDs disambiguate)

### Status payloads and user notices travel separately

**Choice**: Completion and failure emit two things: user-facing text via `app.channels.deliver`, and a structured `{ source, instanceId, status, message }` payload on the tasks extension's own `tasks:instance-finished` app event (the `notify` event is owned by notifications/detached-processes; tasks never emit to it). Cross-extension consumers subscribe to `tasks:instance-finished`; this is not a user notification.
**Why**: User notices need task-specific formatting and idle gating now; the bus payload is a machine-readable signal for future consumers (e.g. task-aware context providers) without coupling tasks to the notifications format or double-delivering to the user.
**Consequences**:
- Pro: Tasks own their user-facing wording; bus consumers get typed-ish data, not prose
- Con: Two emission points per outcome must be kept in sync; the shared event name invites confusion (mitigated by the router's quiet-skip rule)

## System Behavior

### Scenario: missed cron occurrence over a restart

**Given**: A daily cron definition with `lastFiredAt` from yesterday, and the process was down when today's occurrence passed
**When**: The first `tasks-tick` after startup runs
**Then**: `nextCronRun(lastFiredAt)` yields today's missed occurrence, which is ≤ now, so exactly one catch-up instance is created and `lastFiredAt` advances — subsequent ticks create nothing for that period.

### Scenario: schedule edited on an already-fired definition

**Given**: A cron definition that has fired before, whose schedule the agent updates
**When**: `update_task` applies the patch
**Then**: `lastFiredAt` resets to null and `since` is stamped to now; the next tick anchors at the current hour but advances past `since`, so only occurrences after the edit can fire.

### Scenario: background run completes on the second iteration

**Given**: A pending background instance
**When**: The runner dispatches it; iteration 1 returns mid-workflow text (`continue`), iteration 2 announces completion (`complete`)
**Then**: A single persistent session was opened once and prompted twice; iteration 2's prompt was a short nudge carrying only the evaluator observation (no excerpt replay); the instance ends `completed` with the evaluator reason as result, the session is disposed and post-processing runs with its transcript path, the final text is delivered idle-gated, and a `completed` status payload is emitted.

### Scenario: more pending background instances than the concurrency cap

**Given**: Five pending background instances and `backgroundMaxConcurrent = 2`
**When**: The runner ticks
**Then**: Only two are dispatched (marked `running` and added to the in-flight map); the loop breaks once the map reaches the cap and the other three stay `pending`. As each in-flight run settles and leaves the map, a later tick dispatches the next pending instances — never more than two run at once.

### Scenario: background run asks the user a question and resumes on reply

**Given**: A running background instance that calls `ask_user("Which inbox — work or personal?")`
**When**: The run returns, then the user later answers via `respond_to_task`
**Then**: On the pause, the instance becomes `waiting` with `piSessionFile` persisted (plus the question and a progress excerpt), the session is disposed, the question is delivered immediately (text carries the instance ID) and a `waiting` status payload is emitted; the evaluator is not consulted and the slot frees. `respond_to_task` stores the trimmed reply as `userResponse`, leaving the instance `waiting`. The next background dispatch pass picks the instance up ahead of pending work, reopens the recorded pi session, prompts only the user's reply (full continuity), and the evaluator loop continues to completion — clearing `question`/`resumeContext`. (A legacy instance with no session file instead replays the excerpt + question + reply into a fresh session.)

### Scenario: a background question is never answered

**Given**: A `waiting` instance produced by `ask_user` whose `updatedAt` is older than `waitTimeoutSeconds`
**When**: The expiration pass runs
**Then**: It is marked `failed` with the timeout reason and a failure notice is delivered — the same sweep that was previously dormant, now driven by a real producer.

### Scenario: a background run wedges in `running`

**Given**: A `running` instance whose `startedAt` is older than `runningTimeoutSeconds` (its detached executor died or stalled and never reached a terminal state)
**When**: The stuck-running sweep runs in the tick
**Then**: It is marked `failed` with an "exceeded running timeout" reason, a failure notice and a `tasks:instance-finished` payload are delivered, and the slot it held is freed for other background work. (Distinct from crash recovery, which fails *all* `running` rows at startup; this sweep catches stalls within a live process.)

### Scenario: a spent one-shot ages past retention

**Given**: An auto-disabled one-shot definition (fired, `enabled = false`) whose only instance completed more than `oneShotRetentionSeconds` ago
**When**: The hourly `tasks-one-shot-cleanup` job runs
**Then**: The instance rows are deleted first (FK), then the definition, and the deletion count is logged. A one-shot still within the window, one with a non-terminal instance, or a recurring cron definition is left untouched.

### Scenario: channel handoff fails for a session task

**Given**: A pending session instance and a delivery function that throws (e.g. no channel up)
**When**: The delivery pass runs
**Then**: The instance briefly transitions to `running`, the throw rolls it back to `pending` with `startedAt` cleared, and the next tick retries.

## Notes

- The `waiting` lifecycle is live: a background run pauses via the `ask_user` tool (the producer), `respond_to_task` (main session) supplies the reply, the background dispatch pass resumes the instance, and the expiration sweep fails an instance whose question goes unanswered past `waitTimeoutSeconds`. State is persisted on `task_instances` via the `question` and `resumeContext` columns (migration `0003_tasks_waiting_resume`) alongside the existing `userResponse`; the persistent session backing the run is recorded in `piSessionFile` (migration `0005_tasks_pi_session_file`) so resume reopens it with full continuity (legacy instances with no session file fall back to `resumeContext` replay).
- Background-task session files accumulate in the workspace sessions dir — each background run writes a persistent pi JSONL transcript and there is no retention sweep for them yet (related to the broader transient-table retention gap). Out of scope here; flag for a future sweep.
- Config lives under `[extensions.tasks]`: `timezone` (falls back to `scheduler.timezone`), `sessionTaskMaxHoldSeconds` (900), `backgroundMaxIterations` (10), `backgroundMaxConcurrent` (3), `waitTimeoutSeconds` (7200), `runningTimeoutSeconds` (1800, the stuck-running threshold), `oneShotRetentionSeconds` (172800 = 48 h, the one-shot retention window). The tick (60 s) and cleanup (3600 s) intervals are module constants.
- The maintenance sweeps reuse the existing timestamp columns (`startedAt`/`updatedAt` on instances, `lastFiredAt`/`completedAt` for retention) — no schema change or migration was needed.
- Background runs use the `processor` model tier and pi built-in tools (`read, bash, edit, write, grep, find, ls`) plus two per-run custom tools, `notify_user` and `ask_user`. They **also** bind the curated set of opted-in extension tools: `executor.ts` opens the persistent session via `side.openBackgroundSession(...)`, which sets `bindBackgroundFactories: true` with no `tools` allowlist, and the task tools factory itself is registered with the `background` session scope (index.ts), so the background-opted factories (skills + `delegate_to_agent`, git, projects, detached-processes, notifications, task tools) are all bound into the session — see the "Background runs are capable, not sandboxed" decision above. `respond_to_task` is the one task tool deliberately kept foreground-only (its interactive factory is registered without `background: true`), since a background run must never answer another instance's waiting question.
