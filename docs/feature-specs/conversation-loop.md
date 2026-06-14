# Conversation Loop

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The conversation loop is the message-handling cycle at the center of the app: channels submit inbound messages to the coordinator, which serializes them through an inbox into one exchange at a time (a message arriving mid-exchange instead steers the live run), runs the inbound middleware chain (where [boundary decisions](boundary-detection.md) close or resume sessions), ensures a long-lived pi `AgentSession`, gathers extension context, streams the exchange back through the active channel, and runs exchange processors. Closing a session — via an extension (boundary's topical or idle decision), dangling recovery, or shutdown — triggers phased post-processing with per-processor completion tracking.

The loop lives in `src/coordinator.ts`, with persistence in `src/sessions/registry.ts` and the channel contract in `src/channels/types.ts`. How pi sessions are constructed and how pi events become domain events is specified in [agent-integration](agent-integration.md).

## User Stories

- As a user, I want my messages handled in order against one continuous conversation so that context carries across exchanges within a topic
- As an extension developer, I want pipeline hooks (inbound middleware, exchange processors, post-processors) so that features compose without touching core
- As an extension developer, I want background-originated output gated to conversation pauses so that proactive messages do not interleave with an active exchange
- As the system, I want sessions tracked in the database so that post-processing and resumption survive restarts

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Inbound messages enqueue via `Coordinator.submit()` and are handled as serial exchanges in arrival order; a message arriving while an exchange is in flight steers the live run instead of starting a new exchange (see R16); a failed exchange is logged and never stops the loop |
| R1 | Channels implement the `Channel` contract (`name`, `start`, `respond`, `deliver`, `stop`) and register by name; the app selects exactly one channel at startup via `--channel` or `channels.default`, failing fast on unknown names |
| R2 | Inbound messages carry text, source channel, receipt timestamp, media attachments (kind, path, optional mime/description), and free-form metadata |
| R3 | Registered inbound middleware runs as a chain before session resolution, receiving the message, the active session record (or null), and `closeSession`/`resumeSession` controls |
| R4 | When no session is active, the coordinator opens a pi `AgentSession` with all registered extension factories bound, creates a database record, emits `session:opened`, and runs session-open hooks with error isolation |
| R5 | Session records persist channel, pi transcript path, summary, last exchange, created/closed/resumed timestamps, and per-processor post-processing state; the registry supports create, get, update, close, reopen, dangling lookup, and resumable listing within a time window |
| R6 | On session resume, the coordinator injects bridging context (summaries of sessions closed since the resumed session's prior close) as a single hidden `bridging-context` message via pi's `before_agent_start`. Extension-contributed context (memory, projects, subsystem usage) is not coordinator-gathered — each extension registers its own context section (`app.agent.use({ contextProvider, sessionScopes })`, see [DES-001](../design/DES-001-unified-extension-api.md)) injected directly through pi |
| R7 | Each exchange streams domain `AgentEvent`s to the active channel's `respond()`; media attachments are rendered into the prompt as an `<attachments>` block |
| R8 | Exchange processors run in parallel after every completed exchange with the session record, user text, and latest assistant text, error-isolated; the coordinator's cached session record is refreshed afterwards |
| R9 | Closing a session disposes the pi session, stamps `closedAt`, emits `session:closed`, and runs post-processing |
| R10 | Post-processors run in phases `main → preFinalize → finalize`, parallel within a phase, error-isolated; per-processor `completed`/`failed` state is recorded on the session row and already-completed processors are skipped |
| R11 | On startup, sessions left open by a previous run are closed and post-processed (dangling recovery) |
| R12 | Resuming closes the current session, reopens the target record (`closedAt` cleared, `lastResumedAt` set), and opens a fresh pi session from the stored transcript file |
| R13 | `closeIfIdle()` closes the active session only when no exchange is in flight (returning whether it closed), so time-based policies in extensions can never dispose a streaming session |
| R14 | Background deliveries gated `immediate` send at once; `idle`-gated deliveries (the default) are held while an exchange is in flight or a session is active, then flushed when the in-flight exchange completes or `maxHoldSeconds` expires. A flush orders held items by descending `priority` (default 0) with a stable sort, so higher-priority items lead and same-priority items keep arrival order, and awaits every send. A delivery's `target` (default `"user"`) selects the dispatch path: `"user"` renders through `channel.deliver()`, while `"agent"` instead re-submits the delivery text to the coordinator inbox as a system-origin message (`origin: "system"`, `boundary: "skip"`) so the agent acts on it as a prompt rather than the channel rendering it |
| R17 | At shutdown the loop sets a `shuttingDown` flag, runs registered `onShutdown` hooks (error-isolated), then awaits a final force-flush of held deliveries before closing the active session. While shutting down, `deliver()` always holds (the immediate-send path is suppressed) so the awaited flush handles everything in order, and an `"agent"`-target delivery falls through to `channel.deliver()` instead of being re-submitted into the no-longer-drained inbox — its text is still surfaced rather than lost. When a session is active, the loop also announces the shutdown to the user: it awaits a `Wrapping up the conversation…` line, closes the session (post-processing progress reported on the same message), then awaits a final `Done` |
| R15 | `status(text)` surfaces pipeline progress as `status` events on the app event bus; the coordinator emits per-provider and per-processor status lines. While shutting down, status is routed to the channel's `shutdownStatus()` (a dedicated persistent message) when available, since the streaming renderer that normally hosts the line is gone |
| R16 | A message submitted while an exchange is in flight (with an active session and non-system origin) is routed into the live run via the pi session's `steer()` instead of being queued. Two leading-slash prefixes opt out of steering: `/queue ` strips the prefix, tags the message `queued`, and waits in the inbox for the next exchange; `/new ` strips the prefix, tags the message `forceNew` (honored downstream by the boundary extension, which closes the active session), and likewise skips steering. A `steer()` failure is logged and the message dropped. `abortExchange()` aborts the in-flight run on request |

## Behaviors

### Message Intake, Serialization, and Steering (R0, R16)

The coordinator owns an in-memory inbox drained by a single promise-woken loop (`Coordinator.run()`); new exchanges never overlap, but a message arriving mid-exchange is steered into the live run rather than queued.

**Acceptance Criteria**:
- Given two messages submitted while idle, when the loop runs, then the second exchange starts only after the first completes
- Given a message arrives while an exchange is in flight (active session, non-system origin, no `/queue` prefix), when `submit()` runs, then it calls the active session's `steer()` with the rendered prompt and does not enqueue a new exchange; a `steer()` rejection is logged and the message dropped
- Given a `/queue `-prefixed message arrives mid-exchange, when `submit()` runs, then the prefix is stripped, the message is tagged `queued`, and it waits in the inbox to run as the next exchange (no steering)
- Given a `/new `-prefixed message arrives mid-exchange, when `submit()` runs, then the prefix is stripped, the message is tagged `forceNew`, and it skips steering to wait in the inbox (the boundary extension then opens a fresh session for it)
- Given `abortExchange()` is called, when an exchange is in flight, then the active session's run is aborted
- Given an exchange throws, when the loop catches it, then the error is logged and the next inbox message is processed normally
- Given the abort signal fires, when the loop exits, then it sets `shuttingDown`, runs the `onShutdown` hooks, awaits a force-flush of held deliveries, and then closes the active session (post-processing runs) — the loop does not resolve until the held deliveries' channel writes settle

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
- Given a middleware sets `message.metadata.handled === true` (e.g. the commands extension fully handled the message), when the chain returns, then `handle()` short-circuits — `ensureSession`, context gathering, `streamPrompt`, and exchange processors are all skipped
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

### Bridging Context Injection (R6)

The coordinator's host-owned pi extension injects only bridging context — there is no per-message provider gathering. Extension context is contributed by each extension's own context section (see [agent-integration](agent-integration.md) / [DES-001](../design/DES-001-unified-extension-api.md)).

**Acceptance Criteria**:
- Given a resumed session, when summaries exist for sessions that closed since its prior close, then they are buffered as a `bridging-context` block (oldest-first)
- Given a buffered bridging block, when pi fires `before_agent_start`, then it is injected as a `<context owner="bridging-context">…</context>` section in a single non-displayed message and the pending buffer is cleared
- Given no resume (or no intervening closed sessions), when an exchange starts, then no bridging message is injected

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
- Given a headless or background run that has no per-session close lifecycle, when `app.sessions.runPostProcessors(context)` is called, then the registered post-processors run once in phase order, error-isolated, with no per-processor completion-state tracking (processors that require a transcript no-op when `transcriptPath` is null)

### Idle Close (R13)

`closeIfIdle()` (alias for `closeActiveSessionIfIdle()`) is the coordinator's safety primitive; the idle *timer* that decides when to call it is owned by the boundary extension (see [boundary-detection](boundary-detection.md)), since extensions cannot see the loop's in-flight state.

**Acceptance Criteria**:
- Given an exchange in flight, when `closeIfIdle()` is called, then nothing closes and `false` is returned; when called while idle with an active session, the session closes (post-processing runs) and `true` is returned
- Given no active session, when `closeIfIdle()` is called, then nothing closes and `false` is returned

### Delivery Gating and Shutdown Drain (R14, R17)

Background-originated output (`app.channels.deliver`) is gated so it lands at conversation pauses.

**Acceptance Criteria**:
- Given a delivery with `gate: "immediate"`, when it is submitted, then `channel.deliver()` is called right away, even mid-exchange
- Given an idle-gated delivery while no exchange is in flight and no session is active, when it is submitted, then it is delivered immediately
- Given an idle-gated delivery while an exchange is in flight or a session is active, when it is submitted, then it is held
- Given held deliveries, when the in-flight exchange completes, then they are flushed ordered by descending `priority`; items of equal priority keep their arrival order (stable sort)
- Given a held delivery with `maxHoldSeconds`, when the hold timer expires, then all held deliveries are force-flushed even mid-exchange
- Given a delivery with `target: "agent"`, when it is sent (after gating), then instead of `channel.deliver()` the coordinator re-submits its text to the inbox as a system-origin message (`origin: "system"`, `boundary: "skip"`), so the agent processes it as a prompt
- Given a delivery with no `target` or `target: "user"`, when it is sent, then `channel.deliver()` renders it through the active channel
- Given `channel.deliver()` throws, when a delivery is sent, then the error is logged; other deliveries are unaffected
- Given the loop is shutting down, when a delivery is submitted (including `gate: "immediate"`), then it is held rather than sent immediately, so the final awaited flush delivers it in order
- Given a held `target: "agent"` delivery, when it is flushed during shutdown, then it is not re-submitted to the inbox (which is no longer drained) but falls through to `channel.deliver()` so its text still reaches the user
- Given the loop is shutting down with an active session, when teardown runs, then the user sees a `Wrapping up the conversation…` line, per-processor `Post-processing: <name>…` progress on the same message, and a final `Done`, each awaited so it lands before the process exits

### Processing Status (R15)

**Acceptance Criteria**:
- Given any component calls `app.status(text)`, when it runs, then a `status` event with the text is emitted on the app event bus, debug-logged, and forwarded to the active channel's optional `status()` (a channel rendering failure is caught and debug-logged)
- Given post-processors run, when each starts, then the coordinator emits a named status line for it
- Given the loop is shutting down and the channel provides `shutdownStatus()`, when a status line is emitted, then it routes to that dedicated persistent message instead of the (now absent) streaming renderer; a channel without `shutdownStatus()` falls back to `status()`
