# Conversation Loop

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The conversation loop is the message-handling cycle at the center of the app: channels submit inbound messages to the coordinator, which serializes them through an inbox, runs the inbound middleware chain (where [boundary decisions](boundary-detection.md) close or resume sessions), ensures a long-lived pi `AgentSession`, gathers extension context, streams the exchange back through the active channel, and runs exchange processors. Closing a session — via boundary, idle timeout, dangling recovery, or shutdown — triggers phased post-processing with per-processor completion tracking.

The loop lives in `src/coordinator.ts`, with persistence in `src/sessions/registry.ts` and the channel contract in `src/channels/types.ts`. How pi sessions are constructed and how pi events become domain events is specified in [agent-integration](agent-integration.md).

## User Stories

- As a user, I want my messages handled in order against one continuous conversation so that context carries across exchanges within a topic
- As an extension developer, I want pipeline hooks (inbound middleware, context providers, exchange processors, post-processors) so that features compose without touching core
- As an extension developer, I want background-originated output gated to conversation pauses so that proactive messages do not interleave with an active exchange
- As the system, I want sessions tracked in the database so that post-processing and resumption survive restarts

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Inbound messages enqueue via `Coordinator.submit()` and are handled strictly one at a time in arrival order; a failed exchange is logged and never stops the loop |
| R1 | Channels implement the `Channel` contract (`name`, `start`, `respond`, `deliver`, `stop`) and register by name; the app selects exactly one channel at startup via `--channel` or `channels.default`, failing fast on unknown names |
| R2 | Inbound messages carry text, source channel, receipt timestamp, media attachments (kind, path, optional mime/description), and free-form metadata |
| R3 | Registered inbound middleware runs as a chain before session resolution, receiving the message, the active session record (or null), and `closeSession`/`resumeSession` controls |
| R4 | When no session is active, the coordinator opens a pi `AgentSession` with all registered extension factories bound, creates a database record, emits `session:opened`, and runs session-open hooks with error isolation |
| R5 | Session records persist channel, pi transcript path, summary, last exchange, created/closed/resumed timestamps, and per-processor post-processing state; the registry supports create, get, update, close, reopen, dangling lookup, and resumable listing within a time window |
| R6 | Context providers run in parallel before each prompt with per-provider error isolation; non-null blocks are injected as a single hidden tagged message via pi's `before_agent_start` |
| R7 | Each exchange streams domain `AgentEvent`s to the active channel's `respond()`; media attachments are rendered into the prompt as an `<attachments>` block |
| R8 | Exchange processors run in parallel after every completed exchange with the session record, user text, and latest assistant text, error-isolated; the coordinator's cached session record is refreshed afterwards |
| R9 | Closing a session disposes the pi session, stamps `closedAt`, emits `session:closed`, and runs post-processing |
| R10 | Post-processors run in phases `main → preFinalize → finalize`, parallel within a phase, error-isolated; per-processor `completed`/`failed` state is recorded on the session row and already-completed processors are skipped |
| R11 | On startup, sessions left open by a previous run are closed and post-processed (dangling recovery) |
| R12 | Resuming closes the current session, reopens the target record (`closedAt` cleared, `lastResumedAt` set), and opens a fresh pi session from the stored transcript file |
| R13 | Sessions with no completed exchange for `sessions.idleCloseSeconds` auto-close, triggering post-processing |
| R14 | Background deliveries gated `immediate` send at once; `idle`-gated deliveries (the default) are held while an exchange is in flight or a session is active, then flushed when the in-flight exchange completes or `maxHoldSeconds` expires |
| R15 | `status(text)` surfaces pipeline progress as `status` events on the app event bus; the coordinator emits per-provider and per-processor status lines |

## Behaviors

### Message Intake and Serialization (R0)

The coordinator owns an in-memory inbox drained by a single promise-woken loop (`Coordinator.run()`); exchanges never overlap.

**Acceptance Criteria**:
- Given two messages submitted in quick succession (including mid-generation), when the loop runs, then the second exchange starts only after the first completes
- Given an exchange throws, when the loop catches it, then the error is logged and the next inbox message is processed normally
- Given the abort signal fires, when the loop exits, then the idle timer is cleared and the active session is closed (post-processing runs)

### Channel Registry and Contract (R1, R2)

Channels are extensions that register a `Channel` implementation; the core selects one and binds it to the coordinator (see [core-shell](core-shell.md) for startup ordering).

**Acceptance Criteria**:
- Given a channel extension calls `app.channels.register(channel)`, when the app starts, then the channel is selectable by its `name`
- Given `--channel <name>` (or `channels.default`) names an unregistered channel, when the app starts, then it fails with an error listing available channels
- Given the selected channel starts, when `start(runtime)` is called, then it receives a component logger and a `submit` function that feeds the coordinator inbox
- Given an inbound message with media, when the prompt is rendered, then attachments are listed as `- <kind> at <path> — <description>` lines inside an `<attachments>` block after the text

### Inbound Middleware (R3)

Middleware composes as a `next()`-style chain ahead of session resolution — this is where [boundary detection](boundary-detection.md) continues, closes, or resumes sessions.

**Acceptance Criteria**:
- Given multiple registered middleware, when a message arrives, then they run in registration order, each controlling whether `next()` proceeds
- Given middleware calls `context.closeSession()`, when it returns, then the active session is closed (post-processing runs) before the exchange opens a fresh session
- Given middleware calls `context.resumeSession(record)`, when it returns, then the resumed session is the active session for the exchange
- Given no session is active (cold start), when middleware runs, then `context.session` is null

### Session Lifecycle (R4, R5, R9, R11, R12)

One database row per conversation; the active pi session and its record travel together as the coordinator's `ActiveSession`.

**Acceptance Criteria**:
- Given no active session, when an exchange starts, then `AgentManager.open()` creates a pi session, `SessionRegistry.create()` records the channel and pi session file, and registered `onOpen` hooks run (a failing hook is logged, the exchange continues)
- Given an active session, when subsequent messages arrive, then the same pi session is prompted (no per-message client)
- Given a session closes, when `closeActiveSession()` runs, then the pi session is disposed, `closedAt` is stamped, `session:closed` is emitted, and post-processing runs to completion
- Given rows with null `closedAt` exist at startup, when `recoverDanglingSessions()` runs, then each is closed and post-processed before the channel starts
- Given a resumable record, when `resumeSession(record)` runs, then the current session is closed first, the record's `closedAt` is cleared with `lastResumedAt` set, and a new pi session opens from `piSessionFile` (`session:opened` with `resumed: true`)
- Given a window in seconds, when `listResumable()` is queried, then only sessions closed after the cutoff are returned, newest first

### Context Gathering and Injection (R6)

Before each prompt, providers contribute tagged blocks that the host-owned pi extension injects as one hidden context message.

**Acceptance Criteria**:
- Given registered context providers, when an exchange starts, then all run in parallel and a per-provider status line (`Gathering context: <name>…`) is emitted
- Given a provider returns null, when blocks are collected, then it contributes nothing
- Given a provider throws, when blocks are collected, then the failure is logged with the provider name and the remaining blocks are still injected
- Given collected blocks, when pi fires `before_agent_start`, then they are joined as `<context owner="<tag>">…</context>` sections in a single non-displayed `tachikoma-context` message, and the pending buffer is cleared

### Exchange Processors (R8)

After each completed prompt cycle, exchange processors (e.g. the rolling summary) react to the new exchange.

**Acceptance Criteria**:
- Given registered exchange processors, when an exchange completes, then all run in parallel with the session record, user text, and the text blocks of the latest assistant message
- Given a processor throws, when results settle, then the failure is logged with the processor name and other processors are unaffected
- Given processors updated the session row (summary, last exchange), when they finish, then the coordinator's cached record is refreshed from the registry

### Post-Processing on Close (R10)

Phased, idempotent processing of the closed session's transcript.

**Acceptance Criteria**:
- Given processors registered across phases, when post-processing runs, then `main` completes before `preFinalize`, which completes before `finalize`; processors within a phase run in parallel
- Given a processor fails, when its phase settles, then it is recorded as `failed`, the error is logged, and later phases still run
- Given a record whose `postProcessingState` already marks a processor `completed`, when post-processing runs again (e.g. dangling recovery), then that processor is skipped
- Given all phases finish, when state is persisted, then `session:post-processed` is emitted with the per-processor state map
- Given a processor runs, when it is invoked, then it receives the session record, the pi transcript path, and a processor-bound child logger

### Idle Close (R13)

**Acceptance Criteria**:
- Given a completed exchange, when `sessions.idleCloseSeconds` elapses with no further exchange, then the session closes and post-processing runs
- Given a new exchange completes before the timeout, when the timer resets, then the idle window restarts
- Given the idle timer is pending, when only it remains, then it does not keep the process alive (`unref`)

### Delivery Gating (R14)

Background-originated output (`app.channels.deliver`) is gated so it lands at conversation pauses.

**Acceptance Criteria**:
- Given a delivery with `gate: "immediate"`, when it is submitted, then `channel.deliver()` is called right away, even mid-exchange
- Given an idle-gated delivery while no exchange is in flight and no session is active, when it is submitted, then it is delivered immediately
- Given an idle-gated delivery while an exchange is in flight or a session is active, when it is submitted, then it is held
- Given held deliveries, when the in-flight exchange completes, then they are flushed in order
- Given a held delivery with `maxHoldSeconds`, when the hold timer expires, then all held deliveries are force-flushed even mid-exchange
- Given `channel.deliver()` throws, when a delivery is sent, then the error is logged; other deliveries are unaffected

### Processing Status (R15)

**Acceptance Criteria**:
- Given any component calls `app.status(text)`, when it runs, then a `status` event with the text is emitted on the app event bus and debug-logged
- Given context providers or post-processors run, when each starts, then the coordinator emits a named status line for it
