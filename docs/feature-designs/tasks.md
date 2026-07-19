# Design: Tasks

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/tasks.md](../feature-specs/tasks.md)
**Status**: Current

## Purpose

This document explains how scheduled tasks are implemented on the unified extension API: the single-tick orchestration, the definition/instance split, queued session delivery as agent turns, and the background goal-driven self-declaration loop over a single persistent pi session per instance.

## Problem Context

pi deliberately provides no scheduling, idle gating, or buffered delivery — those are host concerns ([pi-sdk-notes](../reference/pi-sdk-notes.md)). The tasks extension must express that capability using only the AppContext services an extension is given (DES-001): `app.scheduler`, `app.channels.deliver`, `app.agent.side`, `app.db`, `app.events`.

**Constraints:**
- Extensions cannot reach coordinator internals — the priority queue (tier ordering + idle/max-hold timing) is owned by the conversation loop and reachable only through `app.channels.deliver` with a `tier` (see [conversation-loop.md](../feature-specs/conversation-loop.md))
- Ticks must be cheap and idempotent: the scheduler's overlap protection skips a tick if the previous one is still running, so passes cannot assume exactly-once timing
- Background runs must not block the tick — a slow agent run can span many ticks
- Tests fake `SideRunner` and the delivery function with `Pick<>` types (DES-002), so all logic modules take their dependencies as narrow parameters

**Interactions:**
- Channel delivery (`conversation-loop.md` / [telegram.md](../feature-specs/telegram.md)): session-task instances are injected as agent turns through `app.channels.deliver`
- Notifications extension: background-task notices (failures, the stuck/expired sweeps, the `ask_user` pause) emit a `NotifyPayload` on the shared `notify` app event, so they flow through the notifications router's batching, dedup, severity→tier mapping, and idle gating like every other producer (see [notifications.md](notifications.md)). The tasks extension imports the notify contract (`NOTIFY_EVENT`/`NotifyPayload`/`SEVERITIES`) from `../notifications/payload.ts` and carries no notice event of its own
- Agent manager: `app.agent.side.openBackgroundSession` (a persistent pi session bound with the background factories + custom tools, prompted repeatedly across the loop) and `app.agent.side.classify` (structured extraction — derives the run's goal at run start) power background execution

## Design Overview

`src/extensions/tasks/index.ts` wires one `tasks-tick` job (`app.scheduler.every`, 60 s) that runs five pure passes in order: `generateDueInstances` (definitions → pending instances), `expireWaitingInstances` (waiting timeout sweep), `failStuckRunningInstances` (running timeout sweep), `deliverSessionTasks` (pending session instances → Normal-tier channel delivery), and `BackgroundRunner.tick()` (pending background instances → detached goal-driven self-declaration executions). A second, slower `tasks-one-shot-cleanup` job (`app.scheduler.every`, 3600 s) runs `cleanupExpiredOneShots` (retention pruning). Each pass is a standalone module taking its dependencies (`repository`, `deliver` for session delivery, `emit` for background notices, `side`, `now`, `log`) as parameters; `index.ts` contains no logic beyond wiring and the crash-recovery bootstrap hook.

```
tasks-tick (60s)
  ├─ generation.ts    definitions ──→ pending instances
  ├─ expiration.ts    waiting > timeout (unanswered) ──→ failed (+ notice)
  ├─ stuck-running.ts running > runningTimeout ──→ failed (+ notice, frees slot)
  ├─ session-delivery.ts  pending session ──→ channels.deliver(Normal) ──→ completed
  └─ executor.ts      answered waiting + pending background ──→ run-start goal extraction (fresh runs only) + prompt loop (detached)
                        update_goal tool ──→ completed (result=summary) | not_completable (fail + warning notice)
                        ask_user tool ──→ waiting (+ urgent question on notify event)
                        non-terminal turn ──→ completion nudge (next prompt)
                        cap reached undeclaring ──→ failed (sole auto-fail)

tasks-one-shot-cleanup (3600s)
  └─ one-shot-cleanup.ts  aged auto-disabled one-shots (all instances terminal) ──→ deleted
```

A background run can pause for input: the in-run `ask_user` tool flips the instance to `waiting`, persists the question and a progress excerpt, surfaces the question to the user, and the run returns (freeing its slot). The main-session `respond_to_task` tool stores the user's reply; the next background dispatch pass resumes the instance ahead of fresh pending work, replaying the persisted progress + question + reply into a resume prompt. An unanswered `waiting` instance is failed by the expiration sweep — the producer that makes that sweep live.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/tasks/index.ts` | Wiring: config, crash-recovery bootstrap, tick orchestration, tool registration | Per-minute `tasks-tick` job plus a slower hourly `tasks-one-shot-cleanup` job, instead of per-concern loops |
| `src/extensions/tasks/schema.ts` | Drizzle tables and type maps | Schedule stored as a JSON discriminated union (`cron` expression / `once` ISO instant); `since` and `updatedAt` columns anchor stale-cron prevention and the waiting sweep; `question` + `resumeContext` columns persist a paused run's blocking question and progress excerpt (legacy-resume fallback), `userResponse` carries the reply that triggers resume; `piSessionFile` (migration `0005_tasks_pi_session_file`) records the persistent pi session backing the run — resumed across iterations and `ask_user` pauses, and fed to memory extraction on completion; `goal` (migration `0009_tasks_goal`) is a nullable free-text column on both tables — the definition goal and its per-instance snapshot (null until provided or extracted) |
| `src/extensions/tasks/repository.ts` | All SQL access | Stamps `since` (definitions) and `updatedAt` (instances) on every update; period-aware duplicate query keyed on exact `scheduledFor`; `resolveDefinition` (ID then exact name), `deleteDefinition`, and `getLatestInstanceForDefinition` back the get/delete/run-now tools; `getResumableInstances` returns `waiting` instances whose `userResponse` has arrived; `listStuckRunningInstances` filters `running` rows on `startedAt ?? updatedAt`; `pruneExpiredOneShotDefinitions` deletes aged auto-disabled one-shots whose instances are all terminal (anchored on latest `completedAt`, else `lastFiredAt`); `setDefinitionGoalIfNull` writes an extracted goal back to a definition with one conditional UPDATE (`id` matches AND `goal` is null) so the no-clobber guard is atomic |
| `src/extensions/tasks/schedule.ts` | Parse and format schedules | A croner probe distinguishes cron from one-shot input (`getPattern()` is undefined for datetimes); timezone handling delegated entirely to croner |
| `src/extensions/tasks/generation.ts` | One generation pass | Anchor = `lastFiredAt`, else current hour start minus 1 s; advances past `since` instead of skipping the definition; both passes snapshot `definition.goal` onto the instance |
| `src/extensions/tasks/session-delivery.ts` | One session-delivery pass | Marks the instance `completed` at handoff; rolls back to `pending` if the handoff throws |
| `src/extensions/tasks/executor.ts` | Background goal-driven self-declaration loop + `BackgroundRunner` dispatcher | One persistent `side.openBackgroundSession(...)` opened per execution (resumed from `piSessionFile` when present) — binds the curated background factories with no hard tool allowlist so their tools stay active; the session file is recorded immediately. At run start a fresh (non-resuming) run with no snapshotted goal derives one from the prompt via `side.classify` (`classify` is extraction-only now), stores it on the instance, and writes it back to the definition only while its goal is still null (`setDefinitionGoalIfNull`); resumed instances already have their goal. The opening prompt is task + goal + the declare instruction (workspace context arrives via the background-scoped extension context sections' `before_agent_start`, not a manual fold). Two per-run closure-flag custom tools — `ask_user` and `update_goal` — each have an `execute` handler that captures the agent's intent in a variable the loop reads after the turn: `ask_user` signals a pause the loop turns into a `waiting` transition (persisting `piSessionFile`) before disposing the session; `update_goal` (a `StringEnum` `status` discriminator + `Type.Optional` per-variant fields, validated by throwing from `execute`) sets a terminal declaration. The post-turn dispatch reads those two flags in order — `pendingQuestion` (pause) → `pendingDeclaration` (`completed` → `completed` with the summary as `result` + dispose + `runPostProcessors`, no notice; `not_completable` → `fail()` with the reason in the notice) → neither (inject the completion nudge as the next prompt). The iteration cap is the sole automatic `fail()` for a run that never declares; a resumed instance prompts only the user's reply (legacy instances with no session file fall back to a resume-prompt replay); a `try/finally` guarantees disposal on every exit path; in-flight map prevents double dispatch and caps concurrency at `backgroundMaxConcurrent`; an `AbortController` per in-flight instance backs `BackgroundRunner.cancel(id)`, and `executeBackgroundInstance` takes an optional `AbortSignal` whose listener calls `session.abort()` (the only way to end a mid-flight turn — `prompt()` has no abort parameter and resolves gracefully) and whose `aborted` flag breaks the loop cleanly, writing no status (`stop_task` owns the terminal write) |
| `src/extensions/tasks/expiration.ts` | Waiting-instance timeout sweep | Threshold on `updatedAt`; `onExpired` callback lets `index.ts` emit the `warning`-severity notice on the `notify` event; now has a live producer (`ask_user`) |
| `src/extensions/tasks/stuck-running.ts` | Stuck-running timeout sweep | Mirrors `expiration.ts`: fails `running` instances past `runningTimeoutSeconds`, freeing their concurrency slot; `onStuck` callback lets `index.ts` emit the `warning`-severity notice on the `notify` event |
| `src/extensions/tasks/one-shot-cleanup.ts` | One-shot retention pruning | Thin wrapper over `pruneExpiredOneShotDefinitions`; runs on the slower cleanup job since retention is low-churn |
| `src/extensions/tasks/tools.ts` | Agent-facing tools | Pure handlers over `ToolDeps`; the pi factory only wraps them in `registerTool` calls; `run_task_now` creates a pending instance and returns — dispatch is the existing tick path, not a direct executor call; `respond_to_task` stores a reply on a `waiting` instance and lets the dispatch pass resume it; `stop_task` cancels a non-terminal instance to `failed` (the cancel initiator owns the terminal write) and asks the runner to abort a live run via the optional `cancelRunningInstance` dep |

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
- Pro: Episodic/topics memory of background tasks is now extracted — the persistent session writes a real pi transcript that completion post-processing feeds to memory extraction (see "Persistent pi session per background instance" below)

### Session tasks are injected as agent turns, completed at handoff

**Choice**: `deliverSessionTasks` renders the task prompt into a labelled text block and hands it to `app.channels.deliver` with `tier: "normal"` and `metadata { kind: "session_task", instanceId }`; the instance is marked `completed` as soon as the handoff succeeds. Every background delivery is agent-targeted now, so the coordinator injects the queued item as a fresh turn into the active session (a system-origin `submit` with `boundary: "skip"`), and the agent acts on the prompt and the user sees its response.
**Why**: Channel delivery is the only proactive-output surface DES-001 gives extensions, and the coordinator routes every delivery into the live session as a turn without the extension touching session internals. The conversation loop still owns tier ordering and timing, so the pass stays a pure detector/enqueuer.
**Alternatives Considered**: A dedicated buffer extension that owns idleness itself (the conversation loop's priority queue covers it).
**Consequences**:
- Pro: No duplicated idle logic; tier ordering and timing are uniform with notifications
- Pro: Rollback-on-throw keeps the instance retryable without a stuck `running` row
- Pro: The agent acts on the task prompt, so session tasks can drive real work rather than only relaying canned text
- Con: "Completed" means "handed to the delivery queue", not "the agent's response reached the user"; a queued delivery lost at shutdown is not retried

### Persistent pi session per background instance

**Choice**: `executeBackgroundInstance` opens ONE persistent pi session per execution via `app.agent.side.openBackgroundSession({ system, customTools, sessionFile })` (a thin `SideRunner` method over `AgentManager.open` with `inMemory` off, `bindBackgroundFactories: true`, and no `tools` allowlist), records its `sessionFile` on the instance immediately, then prompts it once per iteration. The opening prompt is just the task (workspace context arrives via the background-scoped context sections); each non-terminal continuation is the completion nudge (a user message re-evaluating the goal) — the session retains its own tool-call and file-state history, so no excerpt replay is needed. A `try/finally` disposes the session on every exit path.
**Why**: A real pi session is the same primitive the main conversation resumes (`Coordinator.resumeSession` → `AgentManager.open({ sessionFile })`), so background continuity is plumbing, not a pi limitation. Full session continuity preserves tool-call history and intermediate file knowledge across iterations (the old per-iteration ephemeral run replayed only a 4k-char text excerpt and lost everything else), and the session's pi JSONL transcript lets completion post-processing run episodic/topics memory extraction — previously impossible because no transcript existed.
**Alternatives Considered**: The original ephemeral in-memory `side.run` per iteration with prompt-replayed continuity (lost tool/file state and produced no transcript); keeping the session in memory only (no transcript for extraction, no crash-resumable file).
**Consequences**:
- Pro: Full continuity across iterations — tool-call history and file state survive, not just a text excerpt
- Pro: Completion feeds a real transcript to memory extraction, so background work lands in episodic/topics memory
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

**Choice**: A background run pauses by calling the in-run `ask_user` custom tool. The tool records the question in a closure flag and returns a "stop and wait" message; after the prompt returns, the executor — seeing the flag set — transitions the instance to `waiting`, persisting `piSessionFile` (alongside the question and the run's final text as `resumeContext`), disposes the session, emits the question as an `urgent`-severity notice on the `notify` event (instance ID in the text; the router queues it at the Urgent tier), then returns (the turn is paused, not terminated). The main-session `respond_to_task` tool stores the trimmed reply as `userResponse`; `BackgroundRunner.tick` dispatches `getResumableInstances` (answered `waiting`) ahead of `getPendingInstances`, and `executeBackgroundInstance` reopens the recorded `piSessionFile` and prompts only the user's reply — the resumed session has full continuity. A legacy instance with no `piSessionFile` (predating persistent sessions) falls back to a fresh session prompted with a resume prompt replaying `resumeContext` + question + reply. Resume bookkeeping is cleared on completion.
**Why**: With a persistent pi session per instance (decision above), the paused run already has a session file on disk — `SessionManager.open(sessionFile)` is exactly how the main conversation resumes, so re-entering the paused agent with full tool/file continuity is the same plumbing rather than a prompt-replay approximation. The legacy fallback keeps instances created before this change safe: their `piSessionFile` is null, so they still resume via the old excerpt replay. Keeping the pause signal in a closure flag (rather than a thrown sentinel) lets the agent's final turn text serve as the legacy progress excerpt for free, and keeps every DB write in the executor where the rest of the lifecycle transitions live.
**Alternatives Considered**: Continuing to resume purely by prompt replay even with a persistent session (discards the available continuity for no gain); routing the reply as a plain inbound message (nothing would re-associate it with the paused instance).
**Consequences**:
- Pro: The resumed run keeps full tool-call history and file state from before the pause — not just a text excerpt
- Pro: The expiration sweep still has a producer, so an abandoned question is bounded by `waitTimeoutSeconds`
- Pro: `respond_to_task` is a pure guarded handler; the resume itself rides the existing dispatch path (concurrency cap, double-dispatch protection for free)
- Pro: Old `waiting` instances remain resumable through the excerpt-replay fallback — no data migration needed
- Con: Resume latency is one tick interval after the reply is stored
- Con: A session file is held on disk across an arbitrarily long user wait (not swept — see retention gap)

### Goal-driven self-declaration replaces the post-turn evaluator

**Choice**: Completion and continuation are driven by the agent's own `update_goal` tool calls and a per-turn completion nudge — there is no post-turn evaluator, evaluation schema, or fail-open path. A run works an explicit free-text `goal` (snapshotted on the instance at creation) and declares `completed` (restated goal + concrete evidence + summary) or `not_completable` (reason); the iteration cap is the sole automatic fail-point.
**Why**: An external post-turn evaluator has no explicit success check to anchor "done" (it judges each turn loosely against the prompt), fails open on any error (so a flaky classifier can never abort a run and burns every iteration before the cap), and makes the instance `result` its own paraphrase rather than the agent's. Self-declaration anchors "done" on an explicit goal, gives a stuck task a voice (a `not_completable` reason reaches the user), and makes the `result` the agent's own summary or reason. A task that never declares still fails deterministically — at the cap, the single clean fail-point.
**Goal lifecycle**: `goal` is optional on `create_task`/`update_task`/`run_task_now` and snapshotted on the instance at creation. When a run starts with no goal, `extractGoal` derives one from the prompt via the existing `side.classify` structured-extraction pattern and writes it back to the definition **only while its goal is still null** (one conditional UPDATE — never clobbering a goal set by `update_task` or an earlier write-back), so later runs reuse it. This is a lazy, marker-free migration in the spirit of DES-006: a non-null goal is the done signal, there is no backfill pass. Extraction failure or an unusable result (empty/trivial/below a minimum-length heuristic) proceeds on the task-prompt basis, persists nothing, and retries every later run — no give-up counter.
**The declaration tool**: One per-run `update_goal` custom tool, built like `ask_user` — a `defineTool` whose `execute` sets a closure flag (`pendingDeclaration`) the loop reads after the turn. A `StringEnum` `status` discriminator (`completed`/`not_completable`) plus `Type.Optional` per-variant fields — not a `Type.Union` of literals (`anyOf` with no discriminator, and literal unions hurt Google/Gemini API compatibility, per pi-sdk-notes). The "evidence required iff status=completed" rule cannot be expressed in JSON Schema, so the validator closes that gap by **throwing from `execute`** on an incomplete declaration (the DES-002 convention — "Throw from `execute` to signal errors" — which the loop surfaces to the agent for in-run self-correction); the flag is set only for a valid declaration, so the loop trusts any flag it sees.
**Alternatives Considered**: A structured JSON goal `{endState, check, invariants}` (costlier extraction, more malformed-output failure surface); a post-turn evaluator judging goal-met (no explicit check, fail-open, second-hand result); two tools instead of one (muddies the "sole terminal path"); accept-then-reject in the loop after the turn (wastes an iteration, splits validation from the tool).
**Consequences**:
- Pro: An explicit goal anchors completion; a stuck task surfaces a reason; the `result` is the agent's own; one terminal surface; deterministic cap failure.
- Con: The in-tool validator can only check that `evidence` is present, not that it is true — the cap and `stop_task` remain the outer guards; an agent that never declares fails at the cap (the hook a future iteration-limit-escalation delta is expected to intercept).

### `run_task_now` queues, the tick dispatches

**Choice**: `run_task_now` only inserts a `pending` instance with `scheduledFor = now` (by-reference snapshots the definition's prompt and type; ad-hoc carries an inline prompt with `definitionId = null` and a defaulted-`background` type). It does not call the executor or session-delivery passes directly — the next `tasks-tick` picks the instance up exactly as it would a generated one. `get_task` and `delete_task` resolve their target through `repository.resolveDefinition`, which tries an exact ID match then an exact name.
**Why**: Reusing the generation→dispatch path means an on-demand run is indistinguishable from a scheduled firing downstream (same lifecycle, concurrency cap, self-declaration loop, idle gating), so there is no second dispatch surface to keep in sync. Snapshotting the prompt instead of mutating the definition lets an auto-disabled one-shot be re-run without resurrecting its schedule. ID-or-name resolution keeps the tools usable from conversation where the agent often has the human name, not the UUID.
**Consequences**:
- Pro: One dispatch path; an immediate run inherits the concurrency cap and double-dispatch protection for free
- Pro: By-reference runs never mutate the definition, so schedule/enabled/`lastFiredAt` stay authoritative
- Con: Worst-case latency before an on-demand instance starts is one tick interval (60 s); it is not run synchronously
- Con: Name resolution matches the first exact-name row — duplicate names are resolved arbitrarily (IDs disambiguate)

### Background-task notices flow through the notifications pipeline

**Choice**: Failure (a `not_completable` declaration, max iterations, thrown errors), the maintenance sweeps (expired waiting, stuck running), and the `ask_user` pause emit a `NotifyPayload` (`text`, `severity`, `source`) on the shared `notify` app event — the same event the notifications router consumes — rather than calling `app.channels.deliver` directly. Failures and the sweeps use `warning` severity (the router maps `warning` → the Normal delivery tier); the `ask_user` pause uses `urgent` (queued at the Urgent tier with the shortest wait). The `source` is `Background task` (or `Background task: <definition name>` once a definition is known). The emitter is threaded into `BackgroundRunner`/`ExecutorDeps` as an `emit(event, payload)` dependency (`index.ts` wires it to `app.events.emit`), mirroring how `deliver` was threaded. **Successful completion emits nothing** — the running agent surfaces results itself via `notify_user` per the task prompt (R11 in the spec). There is no `tasks:instance-finished` event; it had no consumer.
**Why**: A failure or pause happens when no agent turn is producing output, so it must be raised programmatically; routing it through `notify` rather than a bespoke `deliver` gives background notices the router's batching, dedup (re-emit/retry storms collapse), severity→tier mapping, and idle gating for free, and keeps tasks from re-implementing notice formatting. A success has a live agent that can decide whether the result is worth surfacing, so the executor stays silent there. The notify contract is imported from `../notifications/payload.ts` (an extension→extension import of the contract, consistent with self-update and acceptable per DES-001).
**Consequences**:
- Pro: No duplicate/forced success notice; the agent controls result surfacing per the task's intent
- Pro: Failures, sweeps, and pauses share one delivery pipeline with every other producer — consistent formatting, dedup, and tiering
- Con: A completed task whose agent chooses not to notify finishes silently (intentional); the several notice emission points must keep their `source`/`severity` consistent

### Cancellation reuses `failed`; the cancel initiator owns the terminal write

**Choice**: The `stop_task` tool cancels a task instance from any non-terminal state (`pending`/`running`/`waiting`) by writing `status: "failed"` with a "Task cancelled by user" result — the same `failed`+reason write the stuck-running, waiting-expiration, and crash-recovery terminations already use, rather than introducing a separate `cancelled` status. For a `running` instance the handler then signals the in-process run to abort: `BackgroundRunner` keeps an `AbortController` per in-flight instance (alongside the existing `inFlight` map), `cancel(id)` aborts it and awaits the run, and `executeBackgroundInstance` registers an abort listener that calls pi `session.abort()` — the only way to end a mid-flight turn, since `prompt()` accepts no `AbortSignal` and resolves gracefully (stopReason `aborted`, not a throw) — and checks `signal.aborted` at the top of each iteration and after each `prompt()`, breaking out. The executor's abort path writes **no** status and emits **no** notice: the cancel initiator owns the terminal write, so the executor never clobbers a `failed`-by-cancellation row and there is no race. No programmatic notice is emitted (unlike the sweeps) — the tool is synchronous and user/agent-initiated, so the tool result is the confirmation.
**Why**: The three existing programmatic terminations all reuse `failed`+reason, so a cancellation doing the same keeps one terminal-write pattern and avoids rippling a new status through `TASK_STATUSES`, the `query_task_instances` status enum, `TERMINAL_STATUSES`, the spec lifecycle, and test fixtures; the `result` string already disambiguates "cancelled" from "errored". Having the initiator own the terminal write uniformly across all three non-terminal states (and the executor's abort path be a DB no-op) eliminates any race over the row. `session.abort()` is the SDK's supported interruption primitive, and the listener-plus-loop-check pair covers both a mid-flight abort and one that lands between iterations.
**Alternatives Considered**: A dedicated `cancelled` terminal status (cleaner querying semantics, but establishes a new pattern and adds surface for little gain — `result` already disambiguates); emitting a `warning` notice on cancellation (redundant — the synchronous tool result already confirms, unlike the automatic sweeps that have no agent in the loop).
**Consequences**:
- Pro: One terminal-write pattern; a cancellation is distinguishable via `result` without new schema/enum surface
- Pro: No race — the initiator writes `failed`, the executor just unwinds
- Pro: A running task ends promptly (the in-flight turn is aborted, not left to run to the iteration cap or timeout)
- Con: A cancellation is recorded as `failed`, so callers filtering purely on status cannot separate it from errors without reading `result`

## System Behavior

### Scenario: missed cron occurrence over a restart

**Given**: A daily cron definition with `lastFiredAt` from yesterday, and the process was down when today's occurrence passed
**When**: The first `tasks-tick` after startup runs
**Then**: `nextCronRun(lastFiredAt)` yields today's missed occurrence, which is ≤ now, so exactly one catch-up instance is created and `lastFiredAt` advances — subsequent ticks create nothing for that period.

### Scenario: schedule edited on an already-fired definition

**Given**: A cron definition that has fired before, whose schedule the agent updates
**When**: `update_task` applies the patch
**Then**: `lastFiredAt` resets to null and `since` is stamped to now; the next tick anchors at the current hour but advances past `since`, so only occurrences after the edit can fire.

### Scenario: background run declares completion

**Given**: A pending background instance with a goal
**When**: The runner dispatches it; iteration 1 works without declaring (the loop injects the completion nudge as the next prompt), iteration 2 calls `update_goal({ status: "completed", goalRestated, evidence, summary })`
**Then**: A single persistent session was opened once and prompted twice; the declaration passes in-tool validation; the instance ends `completed` with the agent's summary as `result`; the session is disposed and post-processing runs with its transcript path; and no notice is emitted (the agent may surface results via `notify_user`).

### Scenario: first run of a legacy definition extracts and persists a goal

**Given**: A definition created before goals existed (goal null) and an instance with a snapshotted null goal
**When**: The run starts
**Then**: `extractGoal` derives a goal from the task prompt; if usable, it is stored on the instance and written back to the definition (whose goal is still null), and the opening turn surfaces it. The next run reuses the persisted definition goal and does not re-extract.

### Scenario: write-back is skipped when the definition goal was set in the meantime

**Given**: An instance created while its definition's goal was null, and the definition goal set (by `update_task` or an earlier write-back) before this run's extraction completes
**When**: This instance's run-start extraction completes
**Then**: The derived goal is stored on the instance for this run, but the write-back is skipped — the existing definition goal is never clobbered.

### Scenario: a `not_completable` declaration fails the run with the reason

**Given**: A running background instance that calls `update_goal({ status: "not_completable", reason })`
**When**: The loop processes the declaration after the turn
**Then**: The instance is marked `failed` with the reason as `result`, and a `warning`-severity failure notice carrying the reason is emitted on the `notify` event.

### Scenario: an undeclaring run fails at the cap — the sole auto-fail

**Given**: A running background instance whose agent never calls `update_goal` (and never pauses via `ask_user`)
**When**: The run reaches the iteration cap
**Then**: The instance is marked `failed` with a "Max iterations (N) reached without a terminal declaration" result and a `warning`-severity notice is emitted — the sole automatic failure path, a single `fail()` call.

### Scenario: more pending background instances than the concurrency cap

**Given**: Five pending background instances and `backgroundMaxConcurrent = 2`
**When**: The runner ticks
**Then**: Only two are dispatched (marked `running` and added to the in-flight map); the loop breaks once the map reaches the cap and the other three stay `pending`. As each in-flight run settles and leaves the map, a later tick dispatches the next pending instances — never more than two run at once.

### Scenario: background run asks the user a question and resumes on reply

**Given**: A running background instance that calls `ask_user("Which inbox — work or personal?")`
**When**: The run returns, then the user later answers via `respond_to_task`
**Then**: On the pause, the instance becomes `waiting` with `piSessionFile` persisted (plus the question and a progress excerpt), the session is disposed, the question is emitted as an `urgent`-severity notice on the `notify` event (text carries the instance ID; the router queues it at the Urgent tier); the turn is paused and the slot frees. `respond_to_task` stores the trimmed reply as `userResponse`, leaving the instance `waiting`. The next background dispatch pass picks the instance up ahead of pending work, reopens the recorded pi session, prompts only the user's reply (full continuity), and the self-declaration loop continues to completion — clearing `question`/`resumeContext`. (A legacy instance with no session file instead replays the excerpt + question + reply into a fresh session.)

### Scenario: a background question is never answered

**Given**: A `waiting` instance produced by `ask_user` whose `updatedAt` is older than `waitTimeoutSeconds`
**When**: The expiration pass runs
**Then**: It is marked `failed` with the timeout reason and a `warning`-severity failure notice is emitted on the `notify` event — the same sweep that was previously dormant, now driven by a real producer.

### Scenario: a background run wedges in `running`

**Given**: A `running` instance whose `startedAt` is older than `runningTimeoutSeconds` (its detached executor died or stalled and never reached a terminal state)
**When**: The stuck-running sweep runs in the tick
**Then**: It is marked `failed` with an "exceeded running timeout" reason, a `warning`-severity failure notice is emitted on the `notify` event, and the slot it held is freed for other background work. (Distinct from crash recovery, which fails *all* `running` rows at startup; this sweep catches stalls within a live process.)

### Scenario: a spent one-shot ages past retention

**Given**: An auto-disabled one-shot definition (fired, `enabled = false`) whose only instance completed more than `oneShotRetentionSeconds` ago
**When**: The hourly `tasks-one-shot-cleanup` job runs
**Then**: The instance rows are deleted first (FK), then the definition, and the deletion count is logged. A one-shot still within the window, one with a non-terminal instance, or a recurring cron definition is left untouched.

### Scenario: channel handoff fails for a session task

**Given**: A pending session instance and a delivery function that throws (e.g. no channel up)
**When**: The delivery pass runs
**Then**: The instance briefly transitions to `running`, the throw rolls it back to `pending` with `startedAt` cleared, and the next tick retries.

### Scenario: a task is cancelled with `stop_task`

**Given**: A `running` background instance whose self-declaration loop is mid-flight (or a `pending`/`waiting` instance)
**When**: `stop_task` is called with its instance ID
**Then**: The instance is marked `failed` with a "Task cancelled by user" result (`completedAt` set). For a `running` instance the `BackgroundRunner` aborts the in-flight run's `AbortController`, whose listener calls `session.abort()` to end the current turn; the loop `break`s at the next `signal.aborted` check, disposes the session, and settles without writing status or emitting a notice (the cancel initiator owns the terminal write). A `pending` instance never dispatches; a `waiting` instance never resumes. A terminal (`completed`/`failed`) or unknown ID throws a clear error and changes nothing.

## Notes

- The `waiting` lifecycle is live: a background run pauses via the `ask_user` tool (the producer), `respond_to_task` (main session) supplies the reply, the background dispatch pass resumes the instance, and the expiration sweep fails an instance whose question goes unanswered past `waitTimeoutSeconds`. State is persisted on `task_instances` via the `question` and `resumeContext` columns (migration `0003_tasks_waiting_resume`) alongside the existing `userResponse`; the persistent session backing the run is recorded in `piSessionFile` (migration `0005_tasks_pi_session_file`) so resume reopens it with full continuity (legacy instances with no session file fall back to `resumeContext` replay).
- Background-task session files accumulate in the workspace sessions dir — each background run writes a persistent pi JSONL transcript and there is no retention sweep for them yet (related to the broader transient-table retention gap). Out of scope here; flag for a future sweep.
- Config lives under `[extensions.tasks]`: `timezone` (falls back to `scheduler.timezone`), `backgroundMaxIterations` (10), `backgroundMaxConcurrent` (3), `waitTimeoutSeconds` (7200), `runningTimeoutSeconds` (1800, the stuck-running threshold), `oneShotRetentionSeconds` (172800 = 48 h, the one-shot retention window). Session-task delivery timing is the coordinator's Normal-tier window, not a tasks config. The tick (60 s) and cleanup (3600 s) intervals are module constants.
- The maintenance sweeps reuse the existing timestamp columns (`startedAt`/`updatedAt` on instances, `lastFiredAt`/`completedAt` for retention) — no schema change or migration was needed.
- Background runs use the `processor` model tier and pi built-in tools (`read, bash, edit, write, grep, find, ls`) plus two per-run custom tools, `ask_user` and `update_goal`. They **also** bind the curated set of opted-in extension tools: `executor.ts` opens the persistent session via `side.openBackgroundSession(...)`, which sets `bindBackgroundFactories: true` with no `tools` allowlist, and the task tools factory itself is registered with the `background` session scope (index.ts), so the background-opted factories (skills + `delegate_to_agent`, git, projects, detached-processes, notifications including the canonical `notify_user`, task tools) are all bound into the session — see the "Background runs are capable, not sandboxed" decision above. The executor no longer defines its own `notify_user`; the single canonical one comes from the notifications extension. `respond_to_task` is the one task tool deliberately kept foreground-only (its interactive factory is registered without `background: true`), since a background run must never answer another instance's waiting question.
