# Design: Conversation Loop

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/conversation-loop.md](../feature-specs/conversation-loop.md)
**Status**: Current

## Purpose

Explain how the coordinator orchestrates the conversation lifecycle around a single long-lived pi `AgentSession`, and why the inbox, pipelines, session replacement, and delivery gating are shaped the way they are.

## Problem Context

pi's model is one in-process `AgentSession` per conversation, replaced wholesale on topic boundaries — no per-message client recreation or resume bookkeeping. The coordinator must own everything pi deliberately does not (see `docs/reference/pi-sdk-notes.md`): cross-session lifecycle, the registry, boundary-driven replacement, post-session pipelines, idle gating, and channel delivery — while exposing only the extension hooks defined in [DES-001](../design/DES-001-unified-extension-api.md).

**Constraints:**
- pi's `session.prompt()` resolves only when a full run finishes; a session handles one prompt at a time
- Extension factories bind at session construction — replacement means rebuilding the session, not mutating it
- Pipeline failures (providers, processors, middleware-adjacent hooks) must never break the conversation
- Channels stay thin: they consume `AsyncIterable<AgentEvent>` and never see pi types

**Interactions:**
- [Boundary detection](boundary-detection.md) plugs in as inbound middleware and an exchange processor
- [Agent integration](agent-integration.md) supplies session construction (`AgentManager.open`) and event adaptation (`streamPrompt`)
- [Core shell](../feature-specs/core-shell.md) wires startup order, shutdown, and config defaults (`sessions.*`)
- Extensions reach the loop only through the `app.sessions` / `app.channels` / `app.inbound` / `app.agent` services ([DES-002](../design/DES-002-extension-authoring.md))

## Design Overview

`Coordinator` (`src/coordinator.ts`) is a single serial loop for *new* exchanges: `submit()` pushes into an in-memory inbox and wakes the loop; `handle()` runs the full exchange — inbound middleware chain, `ensureSession`, parallel context gathering into `pendingContext`, `streamPrompt` consumed by `channel.respond()`, parallel exchange processors — with held deliveries flushed in `finally`. After the middleware chain, `handle()` short-circuits if a middleware set `message.metadata.handled === true` (the commands extension's fully-handled path), skipping session resolution, context, streaming, and processors. A message arriving *while an exchange is in flight* does not wait in line: `submit()` routes it into the live run via pi's `session.steer()` (unless it opts out with `/queue ` or `/new `, or is system-origin). Session close (boundary, idle, recovery, shutdown) funnels through `closeActiveSession()`, which disposes the pi session, stamps the registry row, and runs the phased post-processing pipeline. The coordinator owns no idle timer — idle close is a boundary-extension policy (`src/extensions/boundary/idle.ts`) that calls `closeActiveSessionIfIdle()`.

```
submit() ─ mid-exchange & not /queue & not /new & not system? ─→ session.steer(prompt)  (steers the live run)
         └ else → [inbox] → handle():
  middleware (may close/resume; may set metadata.handled → short-circuit) → ensureSession → collectContext ─┐
                                                                  ▼
  channel.respond(streamPrompt(session, prompt)) ← before_agent_start injects <context> blocks
  → exchange processors → flush held deliveries
```

Context reaches the agent through a host-owned pi extension factory (`hostFactory()`, registered in `src/app.ts` alongside extension factories): on `before_agent_start` it drains `pendingContext` into one hidden `tachikoma-context` message.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/coordinator.ts` | Inbox loop, mid-exchange steering (`submit`→`session.steer`), `/queue` opt-out, `abortExchange`, middleware chain, session lifecycle (ensure/close/close-if-idle/resume/recover), pipelines, delivery gating, status emission | Serial *new* exchanges, but mid-exchange input steers the live run; promise-based wake instead of polling; one `ActiveSession` pairing the db record with the live pi session; no coordinator-owned idle timer (a boundary policy drives `closeActiveSessionIfIdle`) |
| `src/sessions/registry.ts` | Drizzle CRUD over the `sessions` table (`src/db/core-schema.ts`) | Synchronous better-sqlite3 access; lifecycle expressed as timestamp updates (`closedAt`, `lastResumedAt`) rather than a state enum |
| `src/channels/types.ts` | `Channel`, `Exchange`, `Delivery` contracts | `respond()` consumes the stream to completion, so channel rendering paces the exchange; `DELIVERY_GATES` const map |
| `src/domain/message.ts`, `src/domain/agent-events.ts` | SDK-free domain types crossing the channel boundary | Channels and extensions never import pi types |
| `src/extensions/host.ts`, `src/extensions/api.ts` | Map `app.sessions` / `app.channels` / `app.inbound` services onto coordinator + registry methods | Registries (`src/extensions/registrations.ts`) are plain mutable arrays filled at setup, read at runtime |
| `src/app.ts` | Wiring: registers `hostFactory()`, recovers dangling sessions after bootstrap, selects and starts the channel, runs the loop until SIGINT/SIGTERM | Channel selection fails fast listing available names |

## Key Decisions

### Serial new exchanges with mid-exchange steering

**Choice**: New exchanges are handled one at a time from the inbox, but a message arriving *while an exchange is in flight* is routed straight into the live run via pi's `session.steer()` rather than queued. `submit()` makes the call: if `exchanging` and an active session exists and the message is not system-origin, it steers; two prefixes opt out — `/queue ` (stripped, tagged `queued: true`, pushed to the inbox to wait for the next exchange) and `/new ` (stripped, tagged `forceNew: true`, pushed to the inbox so the boundary extension opens a fresh session). A separate `abortExchange()` aborts the in-flight run for an explicit user stop.
**Why**: A single-user assistant benefits from being able to redirect a long run ("actually, focus on X") without waiting for it to finish. pi's `steer()` injects the input into the running session, so the user is not blocked on a slow generation. The escape hatches keep the model coherent: `/queue` forces a "wait your turn" fresh exchange when a follow-up should not steer, `/new` likewise waits its turn and starts a clean session, and system-origin injections (e.g. agent-targeted task deliveries) are never treated as steering.
**Alternatives Considered**:
- Strictly serial, no steering: every message waits for the current run — simpler, but a quick redirect is invisible until the run settles
- Concurrent exchanges per channel: no use case for a single-user assistant

**Consequences**:
- Pro: mid-run redirects reach the agent immediately; no blocking on a slow generation
- Pro: new exchanges still get the full middleware + context treatment; lifecycle transitions only happen between exchanges, never racing a steered stream
- Con: steered input bypasses inbound middleware (boundary detection) and context gathering — it joins the current session's run as-is
- Con: a steer failure drops the message (logged); `/queue` is the workaround when the input must run as its own exchange

### Context injection via a host-owned pi extension

**Choice**: Providers fill a `pendingContext` buffer per exchange; `hostFactory()` injects it on `before_agent_start` as a single non-displayed `tachikoma-context` message with `<context owner="…">` sections, then clears the buffer.
**Why**: Keeps the user's prompt text clean, makes context an auditable single message in the transcript, and uses pi's sanctioned injection point instead of mutating `agent.state` by hand.
**Alternatives Considered**:
- Prepending context to the prompt string: pollutes the visible user message and the rolling summary input
- Rebuilding the system prompt per message: pi composes the system prompt at session load, not per prompt

**Consequences**:
- Pro: providers stay pure (`provide() → block | null`); injection is one hook
- Pro: the buffer-drain contract survives session replacement because the factory is rebound with every new session
- Con: blocks gathered for an exchange that fails before `before_agent_start` would linger until the next exchange

### Session replacement by rebuilding, not `AgentSessionRuntime`

**Choice**: Replacing a session (boundary close + open, resume) just disposes the old `AgentSession` and calls `AgentManager.open()` again — pi's `AgentSessionRuntime` is not used.
**Why**: `AgentManager.open()` already rebuilds the resource loader with all registered factories, and event subscriptions are per-exchange (`streamPrompt` subscribes and unsubscribes around each prompt), so there is nothing long-lived to rebind. The runtime's switch/fork machinery solves a problem this design does not have.
**Alternatives Considered**:
- `createAgentSessionRuntime` with `newSession()`/`switchSession()`: handles rebinding, but adds the stale-`pi`/`ctx` footgun documented in `docs/reference/pi-sdk-notes.md` for no gain here

**Consequences**:
- Pro: one construction path for new, resumed, and side sessions
- Pro: no stale-handle class of bugs
- Con: session swap pays full loader reload cost (acceptable: swaps are boundary-rare)

### Per-processor completion state on the session row

**Choice**: `runPostProcessing` records `completed`/`failed` per processor in the record's `postProcessingState` JSON column and skips already-completed processors on re-entry.
**Why**: Dangling recovery re-runs post-processing for sessions closed by a crash; without per-processor state, every recovery would re-extract memories, re-commit, etc. State on the row makes the pipeline idempotent at processor granularity.
**Alternatives Considered**:
- A single `processedAt` timestamp: all-or-nothing, so one failing processor forces rerunning all of them
- A separate processing-log table: more structure than a per-session map needs

**Consequences**:
- Pro: crash-safe, retry-friendly; failed processors retry on the next run while completed ones do not
- Con: a processor renamed between runs is treated as never-run

Headless/background runs that have no per-session close lifecycle reach the same processors through `app.sessions.runPostProcessors(context)` (`SessionsApi.runPostProcessors` → `runPostProcessorsOnce` in `src/extensions/host.ts`), which runs every registered processor once in phase order, error-isolated, with **no** per-processor completion-state tracking — there is no session row to record state on. Transcript-dependent processors no-op when `context.transcriptPath` is null.

### Coordinator-held delivery gating instead of a priority buffer

**Choice**: `deliver()` applies the gate inline — `immediate` sends now; `idle` holds in an array flushed when the in-flight exchange ends, with optional per-item `maxHoldSeconds` force-flush timers. A flush sorts held items by descending `Delivery.priority` (default 0) with a stable comparator, so higher-priority items lead and same-priority items keep arrival order. Dispatch (`sendDelivery`) then branches on `Delivery.target` (default `"user"`): `"user"` calls `channel.deliver()` to render it; `"agent"` instead re-submits the delivery text to the coordinator inbox as a system-origin message (`origin: "system"`, `boundary: "skip"`), so the agent acts on it as a prompt rather than the channel surfacing it to the user.
**Why**: A full priority buffer (a separate subsystem with digests and preemption) is the most complex way to do delivery; the observable requirement is just "don't interleave with an active exchange, order by importance within a flush, never hold forever". The coordinator already knows `exchanging`/`active`, so gating — and the cheap priority sort over the held array — lives where the knowledge is. Producers that care about ordering (e.g. the notifications router mapping severity → priority) set `priority` on the `Delivery`; everything else defaults to 0 and flushes in arrival order.

At shutdown `flushDeliveries` is awaitable (`Promise<void>`, awaiting all sends) so the loop's `finally` does not resolve until the held notices' channel writes settle. The mid-exchange and `maxHoldSeconds` callers stay fire-and-forget (`void`); only the shutdown call awaits.
**Alternatives Considered**:
- A separate priority-buffer extension: rejected — ordering is a one-line stable sort over the held array, not worth a subsystem

**Consequences**:
- Pro: ~20 lines, no extra state machine; channels only implement `deliver()`
- Pro: within a flush, urgent items lead lower-severity ones via the stable priority sort
- Con: a max-hold expiry flushes everything, possibly mid-exchange
- Con: items held while a session is open but quiet wait for the next exchange end (or max-hold) — session close does not flush

### Shutdown drain: flag, hooks, then awaited flush

**Choice**: The loop's `finally` sets `shuttingDown = true`, runs every registered `onShutdown` hook (each awaited in a try/catch via `runShutdownHooks`, error-isolated), then `await flushDeliveries(true)`, then closes the active session. The flag changes two delivery paths: `deliver()` always holds while shutting down (the immediate-send branch is guarded by `!this.shuttingDown`), and `sendDelivery()` guards the `target: "agent"` inbox re-submit with `&& !this.shuttingDown` so an agent-target notice falls through to `channel.deliver()` instead.
**Why**: The inbox loop has already exited by the `finally`, so re-submitting an agent-target delivery would drop it into a dead inbox. Shutdown hooks (e.g. the notifications router's `flushNow`) push their pending output into `heldDeliveries`; holding them behind the flag and letting the single awaited flush drain everything keeps one ordered exit path that the process actually waits on. `onShutdown` mirrors `bootstrap`/`bootstrapHooks` (registered on `Registrations`, exposed on `AppContext`, namespaced `<ext>:<name>`).
**Alternatives Considered**: Delivering from hooks directly (races teardown, no shared ordering); leaving the flush fire-and-forget (the original bug — sends could be cut off by process exit).
**Consequences**:
- Pro: notices held or pending at shutdown reach the user; agent-target text is surfaced rather than lost to the dead inbox
- Con: `immediate` deliveries arriving during shutdown lose their immediacy (held until the final flush)

## System Behavior

### Scenario: User redirects a long run mid-exchange

**Given**: The agent is generating a response in the active session
**When**: The user submits "actually, focus on the second file" (no `/queue` prefix)
**Then**: `submit()` sees `exchanging` with an active non-system session and calls `session.steer(prompt)` — the input joins the running generation without starting a new exchange; a `/queue `-prefixed message instead waits in the inbox to run as the next exchange, and `abortExchange()` would abort the run outright.

### Scenario: Topic shift mid-message

**Given**: An active session with a summary, and a message on a new topic
**When**: Boundary middleware calls `context.closeSession()` and returns `next()`
**Then**: Post-processing for the old session runs to completion inside the middleware chain; `ensureSession` then opens a fresh pi session (factories rebound, `session:opened` emitted) and the exchange proceeds in it.

### Scenario: Crash before close

**Given**: The process died with a session row whose `closedAt` is null and one post-processor previously `completed`
**When**: The next startup calls `recoverDanglingSessions()`
**Then**: The row is closed and post-processing runs with the persisted state — the completed processor is skipped, the rest run, and the merged state is written back.

### Scenario: Background delivery during a conversation

**Given**: An extension calls `app.channels.deliver({ text, maxHoldSeconds: 300 })` while the agent is generating
**When**: The exchange completes (or 300 s pass, whichever is first)
**Then**: The held delivery is sent through `channel.deliver()`; a failure is logged without affecting the loop.

### Scenario: Idle timeout (policy in the boundary extension)

**Given**: A completed exchange and no further messages
**When**: the boundary extension's idle timer (`[extensions.boundary].idleCloseSeconds`, default 900) fires and calls `sessions.closeIfIdle()`
**Then**: The session closes and post-processing runs; the next message starts with no active session, so boundary middleware matches it against resumable sessions (cold-start path). The coordinator contributes only the safety primitive — `closeIfIdle()` refuses while an exchange is in flight, the one piece of loop-internal state extensions cannot see.

## Notes

- `status(text)` both emits a `status` event on the app event bus (debug-logged, no bus subscriber today) and calls the active channel's optional `status()` method; the Telegram channel renders it as a transient line (or a typing action), so status is surfaced to the user through the channel rather than the bus.
- `lastAssistantText` (exchange processor input) concatenates the text blocks of the latest assistant message in the pi session — it does not filter to text after the last tool call.
- The exchange's `try/finally` guarantees `exchanging` resets and held deliveries flush even when the channel's `respond()` throws.
