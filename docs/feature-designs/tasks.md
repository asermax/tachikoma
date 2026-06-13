# Design: Tasks

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/tasks.md](../feature-specs/tasks.md)
**Status**: Current

## Purpose

This document explains how scheduled tasks are implemented on the unified extension API: the single-tick orchestration, the definition/instance split, idle-gated session delivery, and the background evaluator loop over ephemeral pi side sessions.

## Problem Context

pi deliberately provides no scheduling, idle gating, or buffered delivery — those are host concerns ([pi-sdk-notes](../reference/pi-sdk-notes.md)). The tasks extension must express that capability using only the AppContext services an extension is given (DES-001): `app.scheduler`, `app.channels.deliver`, `app.agent.side`, `app.db`, `app.events`.

**Constraints:**
- Extensions cannot reach coordinator internals — idle gating is owned by the conversation loop and reachable only through `app.channels.deliver` with `gate: "idle"` (see [conversation-loop.md](../feature-specs/conversation-loop.md))
- Ticks must be cheap and idempotent: the scheduler's overlap protection skips a tick if the previous one is still running, so passes cannot assume exactly-once timing
- Background runs must not block the tick — a slow agent run can span many ticks
- Tests fake `SideRunner` and the delivery function with `Pick<>` types (DES-002), so all logic modules take their dependencies as narrow parameters

**Interactions:**
- Channel delivery (`conversation-loop.md` / [telegram.md](../feature-specs/telegram.md)): all user-facing output goes through `app.channels.deliver`
- Notifications extension: task status payloads share the `notify` app event but are deliberately not user notifications (see [notifications.md](notifications.md))
- Agent manager: `app.agent.side.run` (ephemeral headless sessions) and `app.agent.side.classify` (structured classification) power background execution

## Design Overview

`src/extensions/tasks/index.ts` wires one `tasks-tick` job (`app.scheduler.every`, 60 s) that runs four pure passes in order: `generateDueInstances` (definitions → pending instances), `expireWaitingInstances` (timeout sweep), `deliverSessionTasks` (pending session instances → idle-gated channel delivery), and `BackgroundRunner.tick()` (pending background instances → detached evaluator-loop executions). Each pass is a standalone module taking its dependencies (`repository`, `deliver`, `side`, `now`, `log`) as parameters; `index.ts` contains no logic beyond wiring and the crash-recovery bootstrap hook.

```
tasks-tick (60s)
  ├─ generation.ts    definitions ──→ pending instances
  ├─ expiration.ts    waiting > timeout (unanswered) ──→ failed (+ notice)
  ├─ session-delivery.ts  pending session ──→ channels.deliver(idle) ──→ completed
  └─ executor.ts      answered waiting + pending background ──→ side.run ⇄ side.classify loop (detached)
                        ask_user tool ──→ waiting (+ question delivered)
```

A background run can pause for input: the in-run `ask_user` tool flips the instance to `waiting`, persists the question and a progress excerpt, surfaces the question to the user, and the run returns (freeing its slot). The main-session `respond_to_task` tool stores the user's reply; the next background dispatch pass resumes the instance ahead of fresh pending work, replaying the persisted progress + question + reply into a resume prompt. An unanswered `waiting` instance is failed by the expiration sweep — the producer that makes that sweep live.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/tasks/index.ts` | Wiring: config, crash-recovery bootstrap, tick orchestration, tool registration | Single every-60s job instead of per-concern loops |
| `src/extensions/tasks/schema.ts` | Drizzle tables and type maps | Schedule stored as a JSON discriminated union (`cron` expression / `once` ISO instant); `since` and `updatedAt` columns anchor stale-cron prevention and the waiting sweep; `question` + `resumeContext` columns persist a paused run's blocking question and progress excerpt, `userResponse` carries the reply that triggers resume |
| `src/extensions/tasks/repository.ts` | All SQL access | Stamps `since` (definitions) and `updatedAt` (instances) on every update; period-aware duplicate query keyed on exact `scheduledFor`; `resolveDefinition` (ID then exact name), `deleteDefinition`, and `getLatestInstanceForDefinition` back the get/delete/run-now tools; `getResumableInstances` returns `waiting` instances whose `userResponse` has arrived |
| `src/extensions/tasks/schedule.ts` | Parse and format schedules | A croner probe distinguishes cron from one-shot input (`getPattern()` is undefined for datetimes); timezone handling delegated entirely to croner |
| `src/extensions/tasks/generation.ts` | One generation pass | Anchor = `lastFiredAt`, else current hour start minus 1 s; advances past `since` instead of skipping the definition |
| `src/extensions/tasks/session-delivery.ts` | One session-delivery pass | Marks the instance `completed` at handoff; rolls back to `pending` if the handoff throws |
| `src/extensions/tasks/executor.ts` | Background evaluator loop + `BackgroundRunner` dispatcher | One ephemeral `side.run` per iteration; continuation prompt carries task, progress excerpt, and evaluator observation; the in-run `ask_user` custom tool signals a pause that the loop turns into a `waiting` transition (persisting question + progress) before the evaluator runs; a resumed instance starts from a resume prompt replaying question + reply; in-flight map prevents double dispatch and caps concurrency at `backgroundMaxConcurrent` |
| `src/extensions/tasks/expiration.ts` | Waiting-instance timeout sweep | Threshold on `updatedAt`; `onExpired` callback lets `index.ts` own user notice + event emission; now has a live producer (`ask_user`) |
| `src/extensions/tasks/tools.ts` | Agent-facing tools | Pure handlers over `ToolDeps`; the pi factory only wraps them in `registerTool` calls; `run_task_now` creates a pending instance and returns — dispatch is the existing tick path, not a direct executor call; `respond_to_task` stores a reply on a `waiting` instance and lets the dispatch pass resume it |

## Key Decisions

### One tick, four pure passes

**Choice**: A single `app.scheduler.every("tasks-tick", 60, ...)` job runs generation, expiration, session delivery, and background dispatch sequentially, each as a pure function over injected dependencies.
**Why**: The core scheduler already provides naming, overlap protection, and error guarding (core-shell R9); reimplementing per-concern async loops would duplicate that. Pure passes with injected `now` are trivially testable against a temp database.
**Alternatives Considered**: Long-lived async loops per concern, coordinated through a shared priority buffer; croner jobs per definition.
**Consequences**:
- Pro: One place to observe all task activity; passes share a transactionless, re-entrant style
- Pro: Tests drive passes directly with a fake clock — no timers
- Con: Worst-case latency for any transition is one tick interval (60 s)
- Con: A pathologically slow pass delays the ones after it within the tick

### Session tasks are injected as agent turns, completed at handoff

**Choice**: `deliverSessionTasks` renders the task prompt into a labelled text block and hands it to `app.channels.deliver` with `gate: "idle"`, `maxHoldSeconds`, and `target: "agent"`; the instance is marked `completed` as soon as the handoff succeeds. The coordinator injects an `agent`-targeted delivery as a fresh turn into the active session (a system-origin `submit` with `boundary: "skip"`), so the agent acts on the prompt and the user sees its response.
**Why**: Channel delivery is the only proactive-output surface DES-001 gives extensions, and its `target` flag lets the coordinator route the rendered prompt into the live session without the extension touching session internals. The conversation loop still owns gating and hold timers, so the pass stays a pure detector/enqueuer.
**Alternatives Considered**: Delivering as plain user-facing text (`target: "user"`) so the prompt is shown verbatim rather than acted on; a dedicated buffer extension that owns idleness itself.
**Consequences**:
- Pro: No duplicated idle logic; delivery ordering and hold behavior are uniform with notifications
- Pro: Rollback-on-throw keeps the instance retryable without a stuck `running` row
- Pro: The agent acts on the task prompt, so session tasks can drive real work rather than only relaying canned text
- Con: "Completed" means "handed to the delivery gate", not "the agent's response reached the user"; a held delivery lost at shutdown is not retried

### Ephemeral side session per background iteration

**Choice**: Each evaluator-loop iteration calls `app.agent.side.run` — an in-memory, bare pi session with filesystem/bash tools that is disposed after the run. Continuity is carried by the continuation prompt: the original task, a 4000-character excerpt of the previous response, and the evaluator's observation.
**Why**: `SideRunner.run` is the sanctioned headless-run primitive (DES-002); keeping one session alive across iterations would require holding pi session state across ticks and rebinding subscriptions. Prompt-carried state keeps each iteration stateless and bounds context growth.
**Alternatives Considered**: One persistent in-memory session per instance, iterated via repeated `session.prompt` calls.
**Consequences**:
- Pro: No session lifecycle to manage; a crashed iteration leaks nothing
- Pro: Context per iteration is bounded regardless of run length
- Con: Tool-call history and intermediate file state knowledge are lost between iterations — only the text excerpt survives

### Concurrency cap on background dispatch

**Choice**: `BackgroundRunner` holds an in-flight `Map<instanceId, Promise>` and, per tick, dispatches pending background instances only while `inFlight.size < maxConcurrent` (`backgroundMaxConcurrent`, default 3); once the cap is hit it breaks out of the loop and leaves the surplus pending. Freed slots are filled on subsequent ticks (or sooner — the runner re-evaluates on every 60 s tick), and the existing in-flight map still prevents double dispatch of a slow run.
**Why**: Dispatching every pending instance was unbounded — a backlog could spawn dozens of simultaneous side runs, each driving an LLM loop, producing an API-request burst and memory pressure. A simple size check against a configurable ceiling bounds the blast radius without needing a queue or a real semaphore primitive, since the tick already re-polls pending work.
**Alternatives Considered**: A promise-based semaphore that admits queued instances the instant a slot frees (vs. waiting for the next tick). Rejected as overkill — the 60 s tick re-poll is a sufficient and simpler drain mechanism, and instances stay durably `pending` in SQLite rather than held in memory.
**Consequences**:
- Pro: Hard ceiling on concurrent side runs / API burst; surplus work stays durably pending in the DB
- Pro: Reuses the existing in-flight map — no new dedup surface
- Con: A freed slot is only refilled on the next tick, so worst-case dispatch latency for a queued instance is one tick interval

### Interactive await/respond resumes by prompt replay, not session resume

**Choice**: A background run pauses by calling the in-run `ask_user` custom tool. The tool records the question in a closure flag and returns a "stop and wait" message; after `side.run` returns, the executor — seeing the flag set — transitions the instance to `waiting`, persisting the question and the run's final text as `resumeContext`, delivers the question to the user (immediate gate, instance ID in the text) and emits a `waiting` status payload, then returns without consulting the evaluator. The main-session `respond_to_task` tool stores the trimmed reply as `userResponse` (leaving the instance `waiting`); `BackgroundRunner.tick` dispatches `getResumableInstances` (answered `waiting`) ahead of `getPendingInstances`, and `executeBackgroundInstance` detects `status === "waiting" && userResponse != null` to build a resume prompt (task + `resumeContext` + question + reply) for the first iteration. Resume bookkeeping is cleared on completion.
**Why**: Side runs are bare, in-memory, and disposed per iteration (DES-002 + the ephemeral-session decision above), so there is no live pi session to suspend and re-enter — persisting a pi session file across an open-ended user wait would mean holding session state durably and rebinding it on resume. The evaluator loop already proves that prompt-carried continuity (task + progress excerpt + observation) is sufficient to resume work; await/respond reuses exactly that mechanism, adding only the question and the user's reply to the replayed context. Keeping the pause signal in a closure flag (rather than a thrown sentinel) lets the agent's final turn text serve as the progress excerpt for free, and keeps every DB write in the executor where the rest of the lifecycle transitions live.
**Alternatives Considered**: Persisting the pi session id / JSONL transcript on the instance and reopening via `SessionManager.open` (`sessionFile`) to truly resume the paused agent. Rejected: it couples tasks to pi session-file lifecycle across arbitrarily long waits, and `BackgroundSide` (the executor's `side` dependency) is a narrow `run`/`classify` surface that tests mock — widening it to expose session handles would leak session internals into the loop for no behavioral gain over replay. Also considered routing the reply as a plain inbound message; rejected because nothing would re-associate it with the paused instance.
**Consequences**:
- Pro: One resume mechanism shared with the evaluator loop; no pi session lifecycle to manage across the wait
- Pro: The expiration sweep finally has a producer, so an abandoned question is bounded by `waitTimeoutSeconds`
- Pro: `respond_to_task` is a pure guarded handler; the resume itself rides the existing dispatch path (concurrency cap, double-dispatch protection for free)
- Con: Tool-call history and intermediate file state from before the pause are lost — only the text excerpt and the Q&A survive into the resumed run
- Con: Resume latency is one tick interval after the reply is stored

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

**Choice**: Completion and failure emit two things: user-facing text via `app.channels.deliver`, and a structured `{ source, instanceId, status, message }` payload on the `notify` app event. The notifications extension deliberately ignores these payloads (no `text` field — see [notifications.md](notifications.md)).
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
**Then**: Iteration 2's prompt embedded the task, iteration 1's excerpt, and the evaluator observation; the instance ends `completed` with the evaluator reason as result, the final text is delivered idle-gated, and a `completed` status payload is emitted.

### Scenario: more pending background instances than the concurrency cap

**Given**: Five pending background instances and `backgroundMaxConcurrent = 2`
**When**: The runner ticks
**Then**: Only two are dispatched (marked `running` and added to the in-flight map); the loop breaks once the map reaches the cap and the other three stay `pending`. As each in-flight run settles and leaves the map, a later tick dispatches the next pending instances — never more than two run at once.

### Scenario: background run asks the user a question and resumes on reply

**Given**: A running background instance that calls `ask_user("Which inbox — work or personal?")`
**When**: The run returns, then the user later answers via `respond_to_task`
**Then**: On the pause, the instance becomes `waiting` with the question and the run's progress excerpt persisted, the question is delivered immediately (text carries the instance ID) and a `waiting` status payload is emitted; the evaluator is not consulted and the slot frees. `respond_to_task` stores the trimmed reply as `userResponse`, leaving the instance `waiting`. The next background dispatch pass picks the instance up ahead of pending work, reopens the run with a resume prompt replaying the progress + question + reply, and the evaluator loop continues to completion — clearing `question`/`resumeContext`.

### Scenario: a background question is never answered

**Given**: A `waiting` instance produced by `ask_user` whose `updatedAt` is older than `waitTimeoutSeconds`
**When**: The expiration pass runs
**Then**: It is marked `failed` with the timeout reason and a failure notice is delivered — the same sweep that was previously dormant, now driven by a real producer.

### Scenario: channel handoff fails for a session task

**Given**: A pending session instance and a delivery function that throws (e.g. no channel up)
**When**: The delivery pass runs
**Then**: The instance briefly transitions to `running`, the throw rolls it back to `pending` with `startedAt` cleared, and the next tick retries.

## Notes

- The `waiting` lifecycle is live: a background run pauses via the `ask_user` tool (the producer), `respond_to_task` (main session) supplies the reply, the background dispatch pass resumes the instance, and the expiration sweep fails an instance whose question goes unanswered past `waitTimeoutSeconds`. State is persisted on `task_instances` via the `question` and `resumeContext` columns (migration `0003_tasks_waiting_resume`) alongside the existing `userResponse`.
- Config lives under `[extensions.tasks]`: `timezone` (falls back to `scheduler.timezone`), `sessionTaskMaxHoldSeconds` (900), `backgroundMaxIterations` (10), `backgroundMaxConcurrent` (3), `waitTimeoutSeconds` (7200). The tick interval is a module constant.
- Background runs use the `processor` model tier and pi built-in tools (`read, bash, edit, write, grep, find, ls`) plus two per-run custom tools, `notify_user` and `ask_user`; Tachikoma extension tools (tasks, notifications) are not bound into side runs.
