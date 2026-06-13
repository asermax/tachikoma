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
  ├─ expiration.ts    waiting > timeout ──→ failed (+ notice)
  ├─ session-delivery.ts  pending session ──→ channels.deliver(idle) ──→ completed
  └─ executor.ts      pending background ──→ side.run ⇄ side.classify loop (detached)
```

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/tasks/index.ts` | Wiring: config, crash-recovery bootstrap, tick orchestration, tool registration | Single every-60s job instead of per-concern loops |
| `src/extensions/tasks/schema.ts` | Drizzle tables and type maps | Schedule stored as a JSON discriminated union (`cron` expression / `once` ISO instant); `since` and `updatedAt` columns anchor stale-cron prevention and the waiting sweep |
| `src/extensions/tasks/repository.ts` | All SQL access | Stamps `since` (definitions) and `updatedAt` (instances) on every update; period-aware duplicate query keyed on exact `scheduledFor` |
| `src/extensions/tasks/schedule.ts` | Parse and format schedules | A croner probe distinguishes cron from one-shot input (`getPattern()` is undefined for datetimes); timezone handling delegated entirely to croner |
| `src/extensions/tasks/generation.ts` | One generation pass | Anchor = `lastFiredAt`, else current hour start minus 1 s; advances past `since` instead of skipping the definition |
| `src/extensions/tasks/session-delivery.ts` | One session-delivery pass | Marks the instance `completed` at handoff; rolls back to `pending` if the handoff throws |
| `src/extensions/tasks/executor.ts` | Background evaluator loop + `BackgroundRunner` dispatcher | One ephemeral `side.run` per iteration; continuation prompt carries task, progress excerpt, and evaluator observation; in-flight map prevents double dispatch |
| `src/extensions/tasks/expiration.ts` | Waiting-instance timeout sweep | Threshold on `updatedAt`; `onExpired` callback lets `index.ts` own user notice + event emission |
| `src/extensions/tasks/tools.ts` | Agent-facing tools | Pure handlers over `ToolDeps`; the pi factory only wraps them in `registerTool` calls |

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

### Evaluator is a strict, best-effort classifier

**Choice**: `evaluateCompletion` classifies the agent response as `complete`/`continue`/`error` using an ordered-rules system prompt that explicitly forbids qualitative review; any evaluator exception is mapped to `continue`.
**Why**: A reviewing evaluator caused quality-gating loops (re-doing finished work); ordered factual rules make "announced completion" terminal even when follow-ups are mentioned. Mapping failures to `continue` means a flaky classifier model can never abort or fail a healthy run — the iteration cap is the backstop.
**Consequences**:
- Pro: Deterministic loop termination semantics; cheap classifier-tier model suffices
- Con: A persistently broken evaluator burns all iterations before failing the task

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

### Scenario: channel handoff fails for a session task

**Given**: A pending session instance and a delivery function that throws (e.g. no channel up)
**When**: The delivery pass runs
**Then**: The instance briefly transitions to `running`, the throw rolls it back to `pending` with `startedAt` cleared, and the next tick retries.

## Notes

- The `waiting` status, `userResponse` column, and expiration sweep exist, but nothing currently transitions an instance into `waiting` — an `await_response`/respond flow has not been built yet; the sweep is dormant until a producer lands.
- Config lives under `[extensions.tasks]`: `timezone` (falls back to `scheduler.timezone`), `sessionTaskMaxHoldSeconds` (900), `backgroundMaxIterations` (10), `waitTimeoutSeconds` (7200). The tick interval is a module constant.
- Background runs use the `processor` model tier and pi built-in tools only (`read, bash, edit, write, grep, find, ls`); Tachikoma extension tools (tasks, notifications) are not bound into side runs.
