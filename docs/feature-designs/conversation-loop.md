# Design: Conversation Loop

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/conversation-loop.md](../feature-specs/conversation-loop.md)
**Status**: Current

## Purpose

Explain how the coordinator orchestrates the conversation lifecycle around a single long-lived pi `AgentSession`, and why the inbox, pipelines, session replacement, and delivery gating are shaped the way they are.

## Problem Context

The Python implementation created a fresh Claude SDK client per message and used `resume` for continuity. pi inverts that model: one in-process `AgentSession` per conversation, replaced wholesale on topic boundaries. The coordinator must own everything pi deliberately does not (see `docs/reference/pi-sdk-notes.md`): cross-session lifecycle, the registry, boundary-driven replacement, post-session pipelines, idle gating, and channel delivery — while exposing only the extension hooks defined in [DES-001](../design/DES-001-unified-extension-api.md).

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

`Coordinator` (`src/coordinator.ts`) is a single serial loop: `submit()` pushes into an in-memory inbox and wakes the loop; `handle()` runs the full exchange — inbound middleware chain, `ensureSession`, parallel context gathering into `pendingContext`, `streamPrompt` consumed by `channel.respond()`, parallel exchange processors, idle-timer reset — with held deliveries flushed in `finally`. Session close (boundary, idle, recovery, shutdown) funnels through `closeActiveSession()`, which disposes the pi session, stamps the registry row, and runs the phased post-processing pipeline.

```
submit() → [inbox] → handle():
  middleware (may close/resume) → ensureSession → collectContext ─┐
                                                                  ▼
  channel.respond(streamPrompt(session, prompt)) ← before_agent_start injects <context> blocks
  → exchange processors → resetIdleTimer → flush held deliveries
```

Context reaches the agent through a host-owned pi extension factory (`hostFactory()`, registered in `src/app.ts` alongside extension factories): on `before_agent_start` it drains `pendingContext` into one hidden `tachikoma-context` message.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/coordinator.ts` | Inbox loop, middleware chain, session lifecycle (ensure/close/close-if-idle/resume/recover), pipelines, delivery gating, status emission | Strictly serial exchanges; promise-based wake instead of polling; one `ActiveSession` pairing the db record with the live pi session |
| `src/sessions/registry.ts` | Drizzle CRUD over the `sessions` table (`src/db/core-schema.ts`) | Synchronous better-sqlite3 access; lifecycle expressed as timestamp updates (`closedAt`, `lastResumedAt`) rather than a state enum |
| `src/channels/types.ts` | `Channel`, `Exchange`, `Delivery` contracts | `respond()` consumes the stream to completion, so channel rendering paces the exchange; `DELIVERY_GATES` const map |
| `src/domain/message.ts`, `src/domain/agent-events.ts` | SDK-free domain types crossing the channel boundary | Channels and extensions never import pi types |
| `src/extensions/host.ts`, `src/extensions/api.ts` | Map `app.sessions` / `app.channels` / `app.inbound` services onto coordinator + registry methods | Registries (`src/extensions/registrations.ts`) are plain mutable arrays filled at setup, read at runtime |
| `src/app.ts` | Wiring: registers `hostFactory()`, recovers dangling sessions after bootstrap, selects and starts the channel, runs the loop until SIGINT/SIGTERM | Channel selection fails fast listing available names |

## Key Decisions

### Strictly serial inbox, no mid-generation routing

**Choice**: One message at a time — input arriving during a generation waits in the inbox until the current `handle()` finishes.
**Why**: pi's `prompt()` owns the session until it settles, and the middleware/context/processor pipeline assumes a stable session per exchange. Serializing makes lifecycle transitions (close, resume, replace) impossible to interleave with streaming.
**Alternatives Considered**:
- Routing mid-generation input via pi's `steer()`/`followUp()` (the DLT-008 ambition): richer UX, but steered input would bypass boundary detection and context gathering entirely
- Concurrent exchanges per channel: no use case for a single-user assistant

**Consequences**:
- Pro: no locks; session replacement can never race a live stream
- Pro: every message gets the full middleware + context treatment
- Con: a quick follow-up ("actually, stop") is not seen until the current run completes

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
- A single `processedAt` timestamp (Python approach): all-or-nothing, so one failing processor forces rerunning all of them
- A separate processing-log table: more structure than a per-session map needs

**Consequences**:
- Pro: crash-safe, retry-friendly; failed processors retry on the next run while completed ones do not
- Con: a processor renamed between runs is treated as never-run

### Coordinator-held delivery gating instead of a priority buffer

**Choice**: `deliver()` applies the gate inline — `immediate` sends now; `idle` holds in a FIFO array flushed when the in-flight exchange ends, with optional per-item `maxHoldSeconds` force-flush timers.
**Why**: The Python priority buffer (priorities, digests, shutdown flush) was the most complex part of delivery; the observable requirement is just "don't interleave with an active exchange, never hold forever". The coordinator already knows `exchanging`/`active`, so gating lives where the knowledge is.
**Alternatives Considered**:
- Porting the priority buffer as an extension: deferred until notification volume justifies ordering and digests

**Consequences**:
- Pro: ~20 lines, no extra state machine; channels only implement `deliver()`
- Con: no priority ordering; a max-hold expiry flushes everything, possibly mid-exchange
- Con: items held while a session is open but quiet wait for the next exchange end (or max-hold) — session close does not flush

## System Behavior

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

- `status` events are emitted on the app event bus and debug-logged; no channel currently subscribes, so granular status is observable in logs but not yet rendered to the user.
- `lastAssistantText` (exchange processor input) concatenates the text blocks of the latest assistant message in the pi session — it does not filter to text after the last tool call as the Python version did.
- The exchange's `try/finally` guarantees `exchanging` resets and held deliveries flush even when the channel's `respond()` throws.
