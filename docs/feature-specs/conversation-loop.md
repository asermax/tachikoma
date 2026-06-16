# Conversation Loop

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The conversation loop is the message-handling cycle at the center of the app: channels submit inbound messages to the coordinator, which serializes them through an inbox into one exchange at a time (a message arriving mid-exchange instead steers the live run), runs the inbound middleware chain (where [boundary decisions](boundary-detection.md) close or resume sessions), ensures a long-lived pi `AgentSession`, gathers extension context, streams the exchange back through the active channel, and runs exchange processors. Closing a session — via an extension (boundary's topical or idle decision), startup recovery, or shutdown — triggers phased post-processing with per-processor completion tracking.

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
| R5 | Session records persist channel, pi transcript path, summary, last exchange, created/closed/resumed timestamps, an `error` (quarantine) flag, and per-processor post-processing state; the registry supports create, get, update, close, reopen, mark-errored, unprocessed lookup, and resumable listing within a time window (resumable listing excludes errored sessions) |
| R6 | On session resume, the coordinator injects bridging context (summaries of sessions closed since the resumed session's prior close) as a single hidden `tachikoma-context` message via pi's `before_agent_start`. Extension-contributed context (memory, projects, subsystem usage) is not coordinator-gathered — each extension registers its own context section (`app.agent.use(provideContext(provide, customType?), { sessionScopes })`, see [DES-001](../design/DES-001-unified-extension-api.md)) injected directly through pi |
| R7 | Each exchange streams domain `AgentEvent`s to the active channel's `respond()`; media attachments are rendered into the prompt as an `<attachments>` block |
| R8 | Exchange processors run in parallel after every completed exchange with the session record, user text, and latest assistant text, error-isolated; processors are skipped for a quarantined (errored) session; the coordinator's cached session record is refreshed afterwards |
| R9 | Closing a session disposes the pi session, stamps `closedAt`, emits `session:closed`, and runs post-processing |
| R10 | Post-processors run in phases `main → preFinalize → finalize`, parallel within a phase, error-isolated; per-processor `completed`/`failed` state is recorded on the session row and already-completed processors are skipped; a quarantined (errored) session skips post-processing entirely |
| R11 | On startup, sessions whose post-processing never completed — whether left open by a crash or closed but interrupted before state persisted — are recovered (closed if still open) and post-processed before the channel starts; an already-closed row is post-processed without restamping `closedAt` |
| R12 | Resuming a quarantined (errored) session is refused — the active session is kept; otherwise resuming closes the current session, reopens the target record (`closedAt` cleared, `lastResumedAt` set), and opens a fresh pi session from the stored transcript file |
| R13 | `closeIfIdle()` closes the active session only when no exchange is in flight (returning whether it closed), so time-based policies in extensions can never dispose a streaming session |
| R14 | Background deliveries (`app.channels.deliver`) are queued, never steering an in-flight exchange. Each carries a `tier` (default `normal`); the coordinator holds them in a priority queue ordered by tier (Urgent → Normal → Low) then FIFO. Per-tier timing governs when the front item — and with it the whole batch — becomes deliverable: an idle window since the last exchange (Urgent 30s, Normal 120s, Low 300s) or a max-hold from enqueue (Urgent 120s, Normal 900s, Low never-force). When the front item is deliverable and no exchange is in flight, all queued items drain into one digest injected as a single system-origin turn (`origin: "system"`, `boundary: "skip"`) the agent surfaces — never a direct channel render. A `Delivery` with `immediate: true` (synchronous command UI, e.g. the `/new` ack) bypasses the queue and renders straight through `channel.deliver()` |
| R15 | Enqueuing a delivery and completing an exchange both re-evaluate the queue against a single shared unref'd timer; a deliverable batch wakes the parked loop (via the digest submit) so it lands promptly at idle, and a non-deliverable queue parks on one timer without busy-spin |
| R17 | At shutdown the loop sets a `shuttingDown` flag, runs registered `onShutdown` hooks (error-isolated), then renders any remaining queued items — tier/FIFO ordered, including those a hook pushes in — as one digest straight through `channel.deliver()` before closing the active session. No agent turn can run during teardown, so this channel render is the one surviving background-render path; queued items are never re-submitted to the no-longer-drained inbox. When a session is active, the loop also announces the shutdown to the user: it awaits a `Wrapping up the conversation…` line, closes the session (post-processing progress reported on the same message), then awaits a final `Done` |
| R18 | An uncaught exception or unhandled rejection drains the active session through the same path as a graceful signal (R17) before the process exits, rather than dying mid-conversation. The shutdown trigger is idempotent: a second exit cause during a drain force-exits immediately. A crash-initiated drain is bounded by a force-exit timeout (autonomous crashes have no external killer) and exits non-zero so a supervisor restarts the process |
| R15 | `status(text)` surfaces pipeline progress as `status` events on the app event bus; the coordinator emits per-provider and per-processor status lines. While shutting down, status is routed to the channel's `shutdownStatus()` (a dedicated persistent message) when available, since the streaming renderer that normally hosts the line is gone. On an idle-timeout close (`closeIfIdle`), the per-processor "Post-processing: …" lines are suppressed and logged only — there is no active renderer and no imminent response to reclaim a channel lead-in, so surfacing them would leave a ghost status line — while `/new`, topic-shift, resume, and shutdown closes still surface them |
| R16 | A message submitted while an exchange is in flight (with an active session and non-system origin) is routed into the live run via the pi session's `steer()` instead of being queued. Two leading-slash prefixes opt out of steering: `/queue ` strips the prefix, tags the message `queued`, and waits in the inbox for the next exchange; `/new ` strips the prefix, tags the message `forceNew` (honored downstream by the boundary extension, which closes the active session), and likewise skips steering. A `steer()` failure is logged and the message dropped. `abortExchange()` aborts the in-flight run on request |
| R19 | When an exchange ends in an encoding-classified error, the active session is marked errored (quarantined) — best-effort, never stopping the loop. A quarantined session is excluded from resumption, its exchange processors are skipped, and its post-processing is skipped, so corrupt output never produces broken derived state |

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
- Given the abort signal fires, when the loop exits, then it sets `shuttingDown`, runs the `onShutdown` hooks, awaits the channel render of the remaining queued items as one digest, and then closes the active session (post-processing runs) — the loop does not resolve until that channel write settles

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

### Session Lifecycle (R4, R5, R9, R11, R12, R19)

One database row per conversation; the active pi session and its record travel together as the coordinator's `ActiveSession`.

**Acceptance Criteria**:
- Given no active session, when an exchange starts, then `AgentManager.open()` creates a pi session, `SessionRegistry.create()` records the channel and pi session file, and registered `onOpen` hooks run (a failing hook is logged, the exchange continues)
- Given an active session, when subsequent messages arrive, then the same pi session is prompted (no per-message client)
- Given a session closes, when `closeActiveSession()` runs, then the pi session is disposed, `closedAt` is stamped, `session:closed` is emitted, and post-processing runs to completion
- Given rows with null `postProcessingState` exist at startup (left open by a crash, or closed but interrupted before state persisted), when `recoverUnprocessedSessions()` runs, then each is closed if still open and post-processed before the channel starts; an already-closed row is post-processed without restamping `closedAt`
- Given a resumable record, when `resumeSession(record)` runs, then the current session is closed first, the record's `closedAt` is cleared with `lastResumedAt` set, and a new pi session opens from `piSessionFile` (`session:opened` with `resumed: true`)
- Given a window in seconds, when `listResumable()` is queried, then only sessions closed after the cutoff are returned, newest first, excluding any marked errored
- Given an active session, when its exchange ends in an encoding-classified error, then the session is marked errored (`error: true`) and a warning is logged, without stopping the loop
- Given an errored session record, when `resumeSession(record)` is called, then the active session is kept unchanged and a warning is logged

### Bridging Context Injection (R6)

The coordinator's host-owned pi extension injects only bridging context — there is no per-message provider gathering. Extension context is contributed by each extension's own context section (see [agent-integration](agent-integration.md) / [DES-001](../design/DES-001-unified-extension-api.md)).

**Acceptance Criteria**:
- Given a resumed session, when summaries exist for sessions that closed since its prior close, then they are buffered (oldest-first)
- Given a buffered bridging block, when pi fires `before_agent_start`, then it is injected as a single non-displayed `tachikoma-context` message (raw content, no XML wrapper) and the pending buffer is cleared
- Given no resume (or no intervening closed sessions), when an exchange starts, then no bridging message is injected

### Exchange Processors (R8)

After each completed prompt cycle, exchange processors (e.g. the rolling summary) react to the new exchange.

**Acceptance Criteria**:
- Given registered exchange processors, when an exchange completes, then all run in parallel with the session record, user text, and the text blocks of the latest assistant message
- Given a processor throws, when results settle, then the failure is logged with the processor name and other processors are unaffected
- Given processors updated the session row (summary, last exchange), when they finish, then the coordinator's cached record is refreshed from the registry
- Given a quarantined (errored) session, when an exchange completes, then exchange processors are skipped — no summary or last exchange is written

### Post-Processing on Close (R10)

Phased, idempotent processing of the closed session's transcript.

**Acceptance Criteria**:
- Given processors registered across phases, when post-processing runs, then `main` completes before `preFinalize`, which completes before `finalize`; processors within a phase run in parallel
- Given a processor fails, when its phase settles, then it is recorded as `failed`, the error is logged, and later phases still run
- Given a record whose `postProcessingState` already marks a processor `completed`, when post-processing runs again, then that processor is skipped (a re-entry guard; startup recovery selects only null-state rows, so it always runs every registered processor)
- Given a closed session marked errored, when post-processing would run (via close or startup recovery), then the entire pipeline is skipped and a warning is logged
- Given all phases finish, when state is persisted, then `session:post-processed` is emitted with the per-processor state map
- Given a processor runs, when it is invoked, then it receives the session record, the pi transcript path, and a processor-bound child logger
- Given a headless or background run that has no per-session close lifecycle, when `app.sessions.runPostProcessors(context)` is called, then the registered post-processors run once in phase order, error-isolated, with no per-processor completion-state tracking (processors that require a transcript no-op when `transcriptPath` is null)

### Idle Close (R13)

`closeIfIdle()` (alias for `closeActiveSessionIfIdle()`) is the coordinator's safety primitive; the idle *timer* that decides when to call it is owned by the boundary extension (see [boundary-detection](boundary-detection.md)), since extensions cannot see the loop's in-flight state.

**Acceptance Criteria**:
- Given an exchange in flight, when `closeIfIdle()` is called, then nothing closes and `false` is returned; when called while idle with an active session, the session closes (post-processing runs) and `true` is returned
- Given no active session, when `closeIfIdle()` is called, then nothing closes and `false` is returned

### Delivery Queue and Shutdown Drain (R14, R15, R17, R18)

Background-originated output (`app.channels.deliver`) is queued and delivered as an agent turn at conversation pauses, ordered by tier.

**Acceptance Criteria**:
- Given a delivery with `immediate: true` and no shutdown in progress, when it is submitted, then `channel.deliver()` renders it right away, bypassing the queue
- Given a queued delivery while no exchange is in flight and there has been no prior exchange, when it is submitted, then it is immediately deliverable (a null idle anchor counts as inherently idle) and drains as a turn
- Given a queued delivery whose front-item idle window has not elapsed since the last exchange, when it is submitted, then it is held and a single shared timer is armed for the next actionable moment
- Given Urgent, Normal, and Low items are queued, when the batch drains, then they are injected in one digest ordered Urgent → Normal → Low, FIFO within a tier
- Given a Normal item enqueued longer ago than its max-hold while the user keeps the idle window from elapsing, when the coordinator is next not exchanging, then it is force-delivered; a Low item is never force-delivered
- Given the front item is deliverable, when the queue drains, then all queued items are submitted as one system-origin turn (`origin: "system"`, `boundary: "skip"`) — never via `session.steer` and never a direct channel render
- Given a queued delivery arrives while the loop is parked, when it becomes deliverable, then the drain's submit wakes the loop; a non-deliverable queue parks on one timer without busy-spin
- Given the loop is shutting down, when queued items remain (including ones an `onShutdown` hook pushes in), then they render to `channel.deliver()` as one tier/FIFO-ordered digest and are never re-submitted to the dead inbox
- Given `channel.deliver()` throws during the shutdown drain, when it is rendered, then the error is logged and teardown proceeds
- Given the loop is shutting down with an active session, when teardown runs, then the user sees a `Wrapping up the conversation…` line, per-processor `Post-processing: <name>…` progress on the same message, and a final `Done`, each awaited so it lands before the process exits
- Given an active session with pending post-processing, when an uncaught exception or unhandled rejection fires, then the session drains (post-processing runs) through the same path as a graceful signal before the process exits non-zero
- Given a drain is already in progress, when a second exit cause arrives (a repeated signal or another error), then the process force-exits immediately and the force-exit is logged
- Given a crash drain has not finished within the force-exit window, when the timeout fires, then the process exits; any session closed but left unprocessed by the interrupted drain is recovered at the next startup (R11)

### Processing Status (R15)

**Acceptance Criteria**:
- Given any component calls `app.status(text)`, when it runs, then a `status` event with the text is emitted on the app event bus, debug-logged, and forwarded to the active channel's optional `status()` (a channel rendering failure is caught and debug-logged)
- Given post-processors run, when each starts, then the coordinator emits a named status line for it
- Given the loop is shutting down and the channel provides `shutdownStatus()`, when a status line is emitted, then it routes to that dedicated persistent message instead of the (now absent) streaming renderer; a channel without `shutdownStatus()` falls back to `status()`
- Given an idle-timeout close (`closeIfIdle`), when post-processing runs, then the per-processor "Post-processing: …" lines are NOT forwarded to the channel (debug-logged instead), so no unreclaimed lead-in is left in the chat — while the processors still run to completion and record their state; `/new`, topic-shift, resume, and shutdown closes still surface the lines
