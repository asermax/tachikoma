# Conversation Loop

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

The conversation loop is the message-handling cycle at the center of the app, built around the **daily trunk**: channels submit inbound messages to the coordinator, which serializes them through an inbox into one exchange at a time (a message arriving mid-exchange instead steers the live run), ensures the day's trunk is live, runs the inbound middleware chain (where [boundary detection](boundary-detection.md) collapses branches, parks tangents, and rolls back decisions), streams the exchange back through the active channel, and runs exchange processors. The trunk closes on a nightly cron or lazily on a stale day — each close runs a phased post-processing pipeline with per-step completion tracking. Shutdown deliberately leaves the trunk open: it persists across restarts, and the memory pipeline runs at the nightly close or next-startup recovery rather than during teardown.

The loop lives in `src/coordinator.ts`, with trunk identity in `src/sessions/trunk.ts` and the channel contract in `src/channels/types.ts`. How pi sessions are constructed and how pi events become domain events is specified in [agent-integration](agent-integration.md). The daily-trunk model is established by [ADR-014](../architecture/ADR-014-session-source-of-truth.md).

## User Stories

- As a user, I want my messages handled in order against the day's continuous conversation so that context carries across exchanges within a topic
- As a user, I want a quick redirect to reach the agent immediately, so I don't wait for a slow run to finish
- As a user, I want to answer a bare command's prompt with my next message instead of typing the argument inline
- As an extension developer, I want pipeline hooks (inbound middleware, exchange processors, post-processors) so features compose without touching core
- As an extension developer, I want background-originated output gated to conversation pauses so proactive messages do not interleave with an active exchange
- As the system, I want trunk state tracked on the session file and an `app_state` pointer so post-processing and recovery survive restarts

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Inbound messages enqueue via `Coordinator.submit()` and are handled as serial exchanges in arrival order; a message arriving while an exchange is in flight steers the live run instead of starting a new exchange (see R16); a failed exchange is logged and never stops the loop |
| R1 | Channels implement the `Channel` contract (`name`, `start`, `respond`, `deliver`, `stop`, optional `status`/`shutdownStatus`/`lifecycleStatus`) and register by name; the app selects exactly one channel at startup via `--channel` or `channels.default`, failing fast on unknown names |
| R2 | Inbound messages carry text, source channel, receipt timestamp, media attachments (kind, path, optional mime/description), and free-form metadata (origin, `boundary`, `forceNew`, `queued`, `forcedBranchId`, `decisionHeader`, etc.) |
| R3 | Registered inbound middleware runs as a `next()`-style chain after the trunk is ensured, receiving the message and an `InboundContext` whose `trunk` is a read-only snapshot of the live trunk (base, branch records, checkpoint, last auto decision); a middleware that sets `message.metadata.handled === true` short-circuits before streaming |
| R4 | `ensureTrunk()` opens today's trunk or creates a fresh one: if the active pointer is same-day and its file exists, it reopens it (re-seating the leaf onto the current base); otherwise it creates a new persisted session, promotes it to active (added to `unclosed` **before** active — the write-ordering invariant), fires `session:opened`, and runs session-open hooks with error isolation |
| R5 | Trunk identity is an `app_state` pointer `{ sessionFile, day, openedAt }`, not a database row; the `sessions` table does not exist. The day is the local calendar date (`YYYY-MM-DD`). A trunk is added to the `unclosed` recovery index before it becomes active and removed only after every completion marker is present, so a crash can never lose a trunk from recovery |
| R6 | On a same-day reopen, pi's `SessionManager.open` sets the leaf to the last file entry rather than the active branch tip; the loop re-seats the leaf onto the boomerang snapshot's `currentTopicBaseId` (or the latest `branch_summary` as a fallback) so the live conversation continues on the current branch |
| R7 | Each exchange streams domain `AgentEvent`s to the active channel's `respond()`; media attachments are rendered into the prompt as an `<attachments>` block. A turn-scoped decision header on the exchange (a `{ label, note, rollbackable }` descriptor set by middleware, e.g. a boundary decision or a rollback) is forwarded to `respond({ header })` and never carried across turns — absent or malformed ⇒ no header |
| R8 | Exchange processors run after every completed (non-handled) exchange with the user text, error-isolated; processors are skipped when the message was handled |
| R9 | Closing a trunk disposes the pi session, emits `session:closed`, and runs post-processing |
| R10 | Post-processors run in phases `main → preFinalize → finalize`, parallel within a phase, error-isolated; per-step/per-branch completion markers on the session file make the pipeline idempotent at fine granularity; a trunk is retired from `unclosed` only after a fully clean close — any failure leaves it for recovery to re-run, skipping the work the markers record |
| R11 | On startup, `recoverStaleTrunks()` runs every trunk in `unclosed` (plus the active pointer if its day precedes today) whose file still exists through the close pipeline, before the channel starts; a recovered trunk's calendar day is its own (the pointer's day, else the session header's creation instant — never defaulted to the recovery day), so a late or multi-day-stale close still attributes its memories to the day they happened |
| R12 | A nightly cron (`scheduler.nightlyCloseHour`, default 4) calls `closeTrunkIfDue()`, which closes the live trunk only when one is active and no exchange is in flight (a cron during a live exchange is skipped — the lazy stale-day backstop closes it next day). Shutdown does not close the trunk — it is left open and reopened by the next process. Before opening today's trunk, a stale active pointer (an earlier day) is closed lazily first |
| R13 | `Coordinator.replay(text, header?)` re-runs `text` as a fresh system-origin turn (`origin: "system"`, `boundary: "skip"`) by enqueueing it at the **front** of the inbox, bypassing `submit()` entirely — so it skips pending-input capture, prefix-stripping, steering, and re-classification; its streamed response carries `header` (used by `/rollback` to re-answer a triggering message under the corrected framing) |
| R14 | Background deliveries (`app.channels.deliver`) are queued, never steering an in-flight exchange. Each carries a `tier` (default `normal`); the coordinator holds them in a priority queue ordered by tier (Urgent → Normal → Low) then FIFO. Per-tier timing governs when the front item — and with it the whole batch — becomes deliverable: an idle window since the last exchange (Urgent 30s, Normal 120s, Low 300s) or a max-hold from enqueue (Urgent 120s, Normal 900s, Low never-force). When the front item is deliverable and no exchange is in flight (and a trunk is live), all queued items drain into one digest injected as a single system-origin turn (`origin: "system"`, `boundary: "skip"`). A `Delivery` with `immediate: true` (synchronous command UI, e.g. an ack) bypasses the queue and renders straight through `channel.deliver()` |
| R15 | Enqueuing a delivery and completing an exchange both re-evaluate the queue against a single shared unref'd timer; nothing drains while an exchange is in flight or before a trunk is live. A held delivery with no live trunk wakes the parked loop to open today's trunk (the first-event-of-the-day path) |
| R16 | A message submitted while an exchange is in flight (active trunk, non-system origin) is routed into the live run via the pi session's `steer()` instead of being queued. Two leading-slash prefixes opt out: `/queue ` strips the prefix, tags the message `queued`, and waits for the next exchange; `/new ` strips the prefix, tags the message `forceNew` (honored by the boundary extension), and likewise skips steering. A `steer()` failure is logged and the message dropped. `abortExchange()` aborts the in-flight run on request |
| R17 | A bare argument-taking command (`/new`, `/queue`, `/skill` with no argument) enters a **pending-input** state: a hardcoded (non-LLM) prompt asks for the argument via the channel's status surface, and the user's next non-command message is captured as that argument and re-dispatched as `<command> <argument>` rather than handled normally. The state is per-chat, in-memory, and ephemeral — cleared by any other slash command (which is then processed normally), by a short configurable TTL (`[coordinator] pendingInputTtlMs`, default 120000), or by a restart. System-origin messages and `replay()` never participate |
| R18 | At shutdown the loop sets a `shuttingDown` flag, runs registered `onShutdown` hooks (error-isolated), then renders any remaining queued items — tier/FIFO ordered — as one digest straight through `channel.deliver()`. The trunk is deliberately left open (not closed): closing it here would run the memory pipeline during teardown, racing a restarting process and redundantly re-pipelining a same-day restart, so the trunk is closed only by the nightly cron or next-startup recovery (ADR-014). No agent turn can run during teardown. When a trunk is active, the loop also announces shutdown: it awaits a `Wrapping up the conversation…` line, then awaits a final `Done` (no close pipeline runs, so there are no per-processor progress lines between them) |
| R19 | An uncaught exception or unhandled rejection drains the held queue and flushes logs through the same path as a graceful signal (R18) before the process exits, rather than dying mid-conversation; the trunk is left open and the day's memory is deferred to next-startup recovery (idempotent via ADR-014's on-file markers), not raced during teardown. The shutdown trigger is idempotent: a second exit cause during a drain force-exits immediately. A crash-initiated drain is bounded by a force-exit timeout and exits non-zero so a supervisor restarts the process |
| R20 | `status(text)` surfaces pipeline progress as `status` events on the app event bus; the coordinator emits per-provider and per-processor status lines, routed to the active channel's `status()` (a reclaimable preparation lead-in). While shutting down, status routes to `shutdownStatus()` (a dedicated persistent message) when available. While a lifecycle trunk-close runs — the nightly `closeTrunkIfDue` or stale-trunk recovery, which have no following exchange to reclaim a lead-in — status routes to `lifecycleStatus()` (a dedicated persistent message, one per close, updated each phase) when available; status from recovery that runs before the channel attaches is buffered and flushed in order once it attaches. `/new`, topic-shift, and resume closes still surface on the reclaimable lead-in |

## Behaviors

### Message Intake, Serialization, and Steering (R0, R16)

The coordinator owns an in-memory inbox drained by a single promise-woken loop (`Coordinator.run()`); new exchanges never overlap, but a message arriving mid-exchange is steered into the live run rather than queued.

**Acceptance Criteria**:
- Given two messages submitted while idle, when the loop runs, then the second exchange starts only after the first completes
- Given a message arrives while an exchange is in flight (active trunk, non-system origin, no `/queue` prefix), when `submit()` runs, then it calls the trunk session's `steer()` with the rendered prompt and does not enqueue a new exchange; a `steer()` rejection is logged and the message dropped
- Given a `/queue `-prefixed message arrives mid-exchange, when `submit()` runs, then the prefix is stripped, the message is tagged `queued`, and it waits for the next exchange
- Given a `/new `-prefixed message arrives mid-exchange, when `submit()` runs, then the prefix is stripped, the message is tagged `forceNew`, and it skips steering to wait for the next exchange (the boundary extensions then collapses and starts a fresh branch)
- Given `abortExchange()` is called, when an exchange is in flight, then the active session's run is aborted
- Given an exchange throws, when the loop catches it, then the error is logged and the next inbox message is processed normally

### Channel Registry and Contract (R1, R2)

**Acceptance Criteria**:
- Given a channel extension calls `app.channels.register(channel)`, when the app starts, then the channel is selectable by its `name`
- Given `--channel <name>` names an unregistered channel, when the app starts, then it fails with an error listing available channels
- Given the selected channel starts, when `start(runtime)` is called, then it receives a component logger and a `submit` function that feeds the coordinator inbox
- Given an inbound message with media, when the prompt is rendered, then attachments are listed as `- <kind> at <path> — <description>` lines inside an `<attachments>` block after the text

### Inbound Middleware and Trunk Snapshot (R3, R7)

Middleware composes as a `next()`-style chain after the trunk is ensured — this is where [boundary detection](boundary-detection.md) collapses branches, parks tangents, and rolls back decisions.

**Acceptance Criteria**:
- Given the trunk is ensured, when middleware runs, then `context.trunk` carries the live session, the current base, topic-filtered branch records, the live branch id, whether the live branch has an assistant turn, the active checkpoint, and the last automatic decision
- Given a middleware sets `message.metadata.handled === true`, when the chain returns, then `handle()` short-circuits — streaming and exchange processors are skipped
- Given a middleware sets `message.metadata.decisionHeader`, when the exchange is not handled, then `respond()` receives it as the turn-scoped header and the next turn does not
- Given no trunk is active when a fully-handled command runs, then `context.trunk` is null and no streaming occurs

### Trunk Lifecycle (R4, R5, R6, R9, R11, R12)

One trunk per local day; the active pi session and its pointer travel together as the coordinator's `ActiveTrunk`.

**Acceptance Criteria**:
- Given no active trunk, when an exchange starts, then a new persisted session is created, promoted to active (added to `unclosed` first), `session:opened` is emitted, and open hooks run (a failing hook is logged, the exchange continues)
- Given an active same-day pointer whose file exists, when `ensureTrunk` runs, then the session is reopened and the leaf re-seated onto the current base, so the live branch continues
- Given a trunk closes, when `closeTrunkSession` runs, then the pi session is disposed, `session:closed` is emitted, and post-processing runs to completion
- Given post-processing has any failure, when the close settles, then the trunk stays in `unclosed` and the next recovery re-runs it (markers skip completed work); given a clean close, then the trunk is retired from `unclosed`
- Given trunks in `unclosed` (or a stale active pointer) at startup, when `recoverStaleTrunks` runs, then each with a surviving file is run through the close pipeline before the channel starts, attributed to its own day
- Given the nightly cron fires while a trunk is active and idle, when `closeTrunkIfDue` runs, then the trunk closes and its post-processing surfaces on a dedicated persistent lifecycle message (updated per phase, ending at "Trunk closed", or "Trunk close failed" on a processor failure); given it fires during an exchange, then it is skipped

### Post-Processing on Close (R10)

Phased, marker-guarded processing of the closed trunk's transcript.

**Acceptance Criteria**:
- Given processors registered across phases, when post-processing runs, then `main` completes before `preFinalize`, which completes before `finalize`; processors within a phase run in parallel
- Given a processor/step fails, when its phase settles, then it is logged, later phases still run, and the trunk is not retired
- Given a step already marked complete on the session file, when post-processing re-runs (e.g. after a crash), then that step is skipped — idempotent at step granularity
- Given a headless/background run with no per-trunk close lifecycle, when `app.sessions.runPostProcessors(context)` is called, then registered post-processors run once in phase order, error-isolated, with no marker tracking (processors that require a transcript no-op when `transcriptPath` is null)

### Pending-Input Flow (R17)

A bare arg-taking command prompts for its argument and captures the next message.

**Acceptance Criteria**:
- Given the user invokes `/new`, `/queue`, or `/skill` bare, when `submit()` runs, then a hardcoded prompt is rendered via the status surface and a per-chat pending state is set; the command is not enqueued
- Given a pending state is active, when the next non-command message arrives, then it is captured as the argument and re-dispatched as `<command> <argument>` (so `/new <arg>` force-collapses via the boundary, `/queue <arg>` waits, `/skill <arg>` flows to pi); the pending state clears
- Given a pending state is active, when a slash command arrives, then the pending state clears and the new command is honored (a different bare arg-command re-enters pending for itself; a non-arg command is processed normally)
- Given a pending state is active past the TTL (or the process restarts), when the TTL elapses, then the pending state clears without capturing a later, unrelated message
- Given the pending-input flow, when it runs over any channel, then it behaves the same (the state/routing lives in the coordinator; the channel only renders the prompt)
- Given `Coordinator.replay()` runs (or a system-origin message arrives), then it is never captured as a pending argument (replay bypasses `submit()`; system-origin messages short-circuit the intercept)

### Decision Header Forwarding and Replay (R7, R13)

**Acceptance Criteria**:
- Given an exchange carries a decision header in metadata, when `respond()` is called, then the header is forwarded and the channel renders it turn-scoped (the streamed text does not overwrite it; it is dropped after the exchange)
- Given `/rollback` (or any caller) invokes `replay(text, header)`, when it runs, then a synthetic system-origin message is enqueued at the front of the inbox, bypassing `submit()` (no pending-input capture, prefix-stripping, steering, or re-classification), and its response carries `header`

### Delivery Queue and Shutdown Drain (R14, R15, R18, R19)

Background-originated output is queued and delivered as an agent turn at conversation pauses, ordered by tier.

**Acceptance Criteria**:
- Given a delivery with `immediate: true` and no shutdown in progress, when submitted, then `channel.deliver()` renders it right away, bypassing the queue
- Given a queued delivery whose front-item idle window has not elapsed, when submitted, then it is held and a single shared timer is armed
- Given Urgent, Normal, and Low items are queued, when the batch drains, then they are injected in one digest ordered Urgent → Normal → Low, FIFO within a tier
- Given the front item is deliverable and a trunk is live, when the queue drains, then all queued items are submitted as one system-origin turn (`origin: "system"`, `boundary: "skip"`) — never via `steer` and never a direct channel render
- Given a held delivery and no live trunk, when it becomes deliverable, then the parked loop is woken to open today's trunk first
- Given the loop is shutting down, when queued items remain (including ones an `onShutdown` hook pushes in), then they render to `channel.deliver()` as one tier/FIFO-ordered digest and are never re-submitted to the dead inbox
- Given the loop is shutting down with an active trunk, when teardown runs, then the user sees a `Wrapping up the conversation…` line and a final `Done` (no close pipeline runs, so there are no per-processor progress lines between them), each awaited so it lands before the process exits
- Given an active trunk, when an uncaught exception or unhandled rejection fires, then the held queue drains and logs flush through the same path as a graceful signal (the trunk is left open; the day's memory is deferred to next-startup recovery) before the process exits non-zero
- Given a drain is already in progress, when a second exit cause arrives, then the process force-exits immediately
- Given a crash drain has not finished within the force-exit window, when the timeout fires, then the process exits; the trunk — left open, not closed — is recovered at the next startup (R11)

### Processing Status (R20)

**Acceptance Criteria**:
- Given any component calls `app.status(text)`, when it runs, then a `status` event is emitted on the app event bus and forwarded to the active channel's optional `status()`
- Given the loop is shutting down, when a status line is emitted, then it routes to `shutdownStatus()` (a dedicated persistent message) when the channel provides it — though no per-processor lines fire during teardown, since the close pipeline does not run at shutdown
- Given a nightly/`closeTrunkIfDue` or stale-trunk-recovery close (no following exchange to reclaim a lead-in), when post-processing runs, then the per-processor lines route to the channel's `lifecycleStatus()` (a dedicated persistent message, one per close) when available — buffered in order and flushed when the channel attaches, for recovery that runs before it — while `/new`, topic-shift, and resume closes still surface on the reclaimable lead-in
