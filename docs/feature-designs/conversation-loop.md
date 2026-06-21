# Design: Conversation Loop

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/conversation-loop.md](../feature-specs/conversation-loop.md)
**Status**: Current

## Purpose

Explain how the coordinator orchestrates the conversation lifecycle around the **daily trunk** — a single append-only pi session per local day — and why the inbox, the trunk pointer/index, the close pipeline, mid-exchange steering, delivery gating, pending-input capture, and rollback replay are shaped the way they are.

## Problem Context

pi's model is one in-process `AgentSession` per conversation. The daily-trunk model ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)) makes that session the source of truth for the whole day: topic shifts are `branch_summary` entries on one append-only tree, and all conversational state lives on the file as pi custom entries. The coordinator owns everything pi deliberately does not (see `docs/reference/pi-sdk-notes.md`): the daily trunk's identity and recovery, boundary-driven collapse, the close pipeline, mid-exchange steering, idle gating, channel delivery, and the DLT-181 surfaces (pending-input capture, decision-header forwarding, rollback replay) — while exposing only the extension hooks defined in [DES-001](../design/DES-001-unified-extension-api.md).

**Constraints:**
- pi's `session.prompt()` resolves only when a full run finishes; a session handles one prompt at a time
- The pi header is not extensible and `sessionsDir` mixes trunk, fork, side-run, and shadow-fork sessions, so trunk discovery needs an explicit `app_state` pointer (directory scanning is unreliable)
- Crash-safe close relies on on-file completion markers plus the `app_state` `unclosed` index (there is no sessions table to carry a processing-state column)
- Channels stay thin: they consume `AsyncIterable<AgentEvent>` and never see pi types; `editMessageText` replaces the full message text, so a turn-scoped header must be recomposed by the renderer on every edit

**Interactions:**
- [Boundary detection](boundary-detection.md) plugs in as inbound middleware and drives branch collapse, checkpoints, and rollback
- [Agent integration](agent-integration.md) supplies session construction (`AgentManager.open`) and event adaptation (`streamPrompt`)
- [Core shell](../feature-specs/core-shell.md) wires startup order, shutdown, the nightly-close cron, and config defaults (`scheduler.nightlyCloseHour`, `[coordinator] pendingInputTtlMs`)
- Extensions reach the loop only through the `app.sessions` / `app.channels` / `app.inbound` / `app.agent` services ([DES-002](../design/DES-002-extension-authoring.md))

## Design Overview

`Coordinator` (`src/coordinator.ts`) is a single serial loop for *new* exchanges. `submit()` is the entry point: a **pending-input intercept** runs first (bare arg-command → prompt, or capture the next message as an argument), then `/queue `/`/new ` prefix-stripping, then — for a message arriving while an exchange is in flight — steering into the live run via `session.steer()`, else enqueue. `handle()` runs the full exchange: `ensureTrunk()` → inbound middleware chain → (if not `handled`) `streamPrompt` consumed by `channel.respond({ header })` → exchange processors. The header is turn-scoped: read fresh from the exchange's metadata, never carried across turns. Trunk close (nightly cron, shutdown, lazy stale-day) funnels through `closeTrunkSession()`, which disposes the session, runs the phased marker-guarded post-processing pipeline, and retires the trunk from `unclosed` only on a clean close.

```
submit() ─ pending-input intercept (bare arg-command → prompt; pending arg → capture)
         ─ mid-exchange & not /queue & not /new & not system? ─→ session.steer(prompt)
         └ else → [inbox] → handle():
  ensureTrunk (open/create today's, lazy stale-day close) → middleware (may collapse/checkpoint/rollback; may set handled → short-circuit) ─┐
                                                                  ▼
  channel.respond(streamPrompt(session, prompt), header)  → exchange processors → re-evaluate the delivery queue
```

`Coordinator.replay(text, header)` (DLT-181) is the rollback seam: it `unshift`s a synthetic system-origin message to the front of the inbox and wakes the loop, bypassing `submit()` entirely so the replayed turn skips pending-input, prefix-stripping, steering, and re-classification.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/coordinator.ts` | Inbox loop; mid-exchange steering (`submit`→`session.steer`); `/queue`/`/new` opt-out; `abortExchange`; pending-input intercept (R17); middleware chain; trunk lifecycle (ensure/nightly/stale/shutdown/recover); close pipeline driver; the delivery priority queue; status emission; `replay()` | Serial *new* exchanges, but mid-exchange input steers the live run; promise-based wake; one `ActiveTrunk` (session + pointer); pending-input state is in-memory and ephemeral; replay routes around `submit()` via inbox `unshift` |
| `src/sessions/trunk.ts` | Trunk identity and on-file state: `TrunkState` (the `app_state` pointer + `unclosed` index), `openOrCreateTrunk`, `BoomerangState`, branch-record enumeration, markers | Pointer + `unclosed` enforce the write-ordering invariant; same-day reopen re-seats the leaf onto the current base; boomerang-state is latest-wins and append-only |
| `src/channels/types.ts` | `Channel`, `Exchange`, `Delivery` contracts | `respond()` consumes the stream to completion, so channel rendering paces the exchange; optional `header` carries the turn-scoped decision descriptor; `DELIVERY_TIERS` const map (per-tier timing in `delivery-queue.ts`) |
| `src/domain/message.ts` | `InboundMessage`, `DecisionHeader`, `decisionHeaderFrom` | SDK-free domain types crossing the channel boundary; the header descriptor rides message metadata |
| `src/extensions/host.ts`, `src/extensions/api.ts` | Map `app.sessions` / `app.channels` / `app.inbound` services onto coordinator methods | `SessionsApi.replay`/`activeTrunkSession`/`onOpen`; `TrunkInbound` snapshot handed to middleware |
| `src/app.ts` | Wiring: recovers stale trunks after bootstrap, selects and starts the channel, registers the nightly-close cron, wires process-exit causes to `ShutdownController`, runs the loop | Nightly close at `scheduler.nightlyCloseHour` |
| `src/shutdown.ts` | `ShutdownController`: routes `SIGINT`/`SIGTERM`/`uncaughtException`/`unhandledRejection` through one idempotent graceful drain | First trigger aborts (the loop's `finally` drains + post-processes); a second trigger force-exits immediately |

## Key Decisions

### Daily trunk: one append-only session per local day

**Choice**: One persistent pi session per local calendar day (the "trunk"), identified by an `app_state` pointer `{ sessionFile, day, openedAt }` rather than a DB row; topic shifts are `branch_summary` entries on that tree; conversational state rides the file as custom entries. The `sessions` table and registry are removed ([ADR-014](../architecture/ADR-014-session-source-of-truth.md)).
**Why**: A single source of truth removes DB↔file dual-write drift; the trunk model's collapse/branch/lookup map directly onto SDK primitives. The pointer (not directory scanning) is the discovery mechanism because `sessionsDir` mixes trunk, fork, side-run, and shadow-fork sessions.
**Alternatives Considered**: A per-topic `sessions` table (the removed model — dual source of truth); directory-scan discovery (unreliable).
**Consequences**: Pro — state reloads natively from the file; no live-row migration. Con — discovery needs the pointer; crash-safe close needs on-file markers + the `unclosed` index.

### Write-ordering invariant for crash-safe close

**Choice**: A trunk is added to the `unclosed` recovery index **before** it becomes `active`, and removed from `unclosed` **only after** every completion marker is present.
**Why**: If the process crashes between creating a trunk and recording it, recovery would never learn about it; adding to `unclosed` first means a crash can never orphan a live trunk. Retiring only on a clean close means a partial failure (e.g. a memory-extraction step that threw) keeps the trunk indexed so the next startup re-runs the close, with the markers skipping work that already completed.
**Consequences**: Pro — no trunk is ever lost from recovery; the close pipeline is idempotent at step granularity. Con — a failing step blocks retirement until recovery re-runs it.

### Same-day reopen re-seats the leaf onto the current base

**Choice**: When reopening a same-day trunk, `openOrCreateTrunk` re-seats the leaf onto the boomerang snapshot's `currentTopicBaseId` (or the latest `branch_summary` as a fallback).
**Why**: pi's `SessionManager.open` sets the leaf to the last file entry, not the active branch tip — so after a restart the next turn would otherwise extend a stale collapsed branch. Re-seating onto the current base keeps the live conversation on the current branch.
**Consequences**: Pro — a restart mid-conversation resumes on the right branch. Con — depends on the append-only `branch(id)` re-seat being stable (tracked in `pi-sdk-notes.md`).

### Serial new exchanges with mid-exchange steering

**Choice**: New exchanges are handled one at a time from the inbox, but a message arriving *while an exchange is in flight* is routed straight into the live run via pi's `session.steer()` rather than queued. `submit()` makes the call: if `exchanging` with an active trunk and a non-system message, it steers; `/queue ` (stripped, tagged `queued`) and `/new ` (stripped, tagged `forceNew`) opt out and wait their turn. A separate `abortExchange()` aborts the run for an explicit stop.
**Why**: A single-user assistant benefits from redirecting a long run ("actually, focus on X") without waiting. The escape hatches keep the model coherent: `/queue` forces a "wait your turn" exchange, `/new` waits and starts a clean branch, and system-origin injections (delivery digests, replays) are never treated as steering.
**Alternatives Considered**: Strictly serial, no steering (a quick redirect is invisible until the run settles); concurrent exchanges (no use case for a single-user assistant).
**Consequences**: Pro — mid-run redirects reach the agent immediately. Con — steered input bypasses inbound middleware and context gathering; a steer failure drops the message (logged) — `/queue` is the workaround.

### Pending-input interception at the top of `submit()`, in-memory and ephemeral

**Choice**: A per-chat transient `Map<chatKey, { command, promptedAt, timer }>` checked at the very top of `submit()` — before prefix-stripping and steering — so a captured argument is never mis-parsed as a command or steered into a live run. A bare arg-taking command (`/new`/`/queue`/`/skill`) renders a hardcoded non-LLM prompt via the status surface; the next non-command message is captured and re-dispatched as `<command> <argument>`. Any slash command cancels pending and is processed normally; a short TTL (`pendingInputTtlMs`, enforced by a clearing `setTimeout` plus a stale-check) or a restart clears it.
**Why**: `submit()` strips prefixes and steers before middleware runs, so the intercept must precede both. Pending state is deliberately ephemeral — a stale prompt across restart would wrongly capture an unrelated message — so it stays in memory. The mechanism lives in the coordinator (which already owns the channel-agnostic `/new`/`/queue` prefix-stripping) so any channel gets parity for free; the channel only renders the prompt.
**Alternatives Considered**: The first inbound middleware (too late — `submit()` strips and steers first); persisting pending state (R17 mandates transience; risks capturing an unrelated post-restart message).
**Consequences**: Pro — channel-agnostic; correct capture ordering. Con — a restart drops a pending prompt (intended); the TTL must stay short.

### Turn-scoped decision header, forwarded from metadata

**Choice**: A middleware sets `message.metadata.decisionHeader = { label, note, rollbackable }`; `handle()` forwards `decisionHeaderFrom(metadata)` to `channel.respond({ header })`, read fresh each exchange and never carried across turns. Manual commands ack directly (they set `handled` and never reach `respond`) so their label rides the ack text; the forwarded header serves decisions that still stream — automatic boundary decisions and rollback replay. The channel renders it per [DES-009](../design/DES-009-turn-scoped-anchored-header.md); the coordinator only produces and forwards the descriptor.
**Why**: Branching decisions should be visible at the moment they happen, and the streaming renderer edits one message in place (so the header must survive recomposition — see [telegram](./telegram.md)), but a header should not linger as a persistent banner. Reading it from per-exchange metadata keeps it turn-scoped by construction.
**Alternatives Considered**: A separate header message (lingers, breaks the single-message stream); carrying the last header across turns (R8 wants turn-scoped surfacing, not a banner).
**Consequences**: Pro — one descriptor feeds both the header and (for auto decisions) `/rollback` eligibility; channels decide how to render it. Con — the renderer owns header composition (contained in the channel).

### Rollback replay routes around `submit()` via inbox `unshift`

**Choice**: `replay(text, header)` builds a synthetic `InboundMessage { origin: "system", boundary: "skip", decisionHeader }`, `unshift`s it to the front of the inbox, and wakes the loop — never calling `submit()`.
**Why**: A replayed triggering message must run verbatim: no pending-input capture, no `/queue`/`/new` prefix-stripping, no mid-exchange steering, and no re-classification (the corrected framing is already applied — `boundary: "skip"` keeps the boundary middleware's classifier from re-running). Front-of-inbox makes it the next serial turn once the rollback command's own (handled) exchange unwinds. `origin: "system"` means it is never itself steered.
**Alternatives Considered**: Routing replay through `submit()` (would re-strip, re-steer, re-capture, and re-classify); running replay inline (would re-enter the exchange loop non-reentrantly).
**Consequences**: Pro — the replayed turn is a clean, non-reentrant fresh turn carrying the rollback header. Con — replay is a power tool that bypasses the usual intake guards (only the rollback flow uses it).

### Tiered priority queue, delivered as an agent turn

**Choice**: Every background delivery is queued and surfaced as an agent turn — there is no direct-to-user path except the `immediate` command-ack escape hatch and the shutdown drain. The timing logic is a pure module (`src/channels/delivery-queue.ts`): a three-tier table (Urgent 30s/120s, Normal 120s/900s, Low 300s/never-force), a `compareQueued` comparator, and `evaluate(now, lastExchangeAt, items)`. The coordinator holds `QueuedItem[]`, stamps `lastExchangeAt` in `handle()`'s `finally`, and runs everything through one idempotent decision point, `scheduleDelivery()`: it clears the single shared unref'd timer, returns early while `shuttingDown`/`exchanging`/empty/before a trunk is live, then either `flushQueue()` (drain) or arms the timer for `wakeAt`. `flushQueue()` builds one digest and `submit()`s it as a system-origin message.
**Why**: The observable requirement is "queue background output, order by importance, deliver as a turn at a pause, never steer, never hold forever, never wake-spin." A single timer + a pure `evaluate` keeps the decision in one method where the coordinator already knows `exchanging`/`lastExchangeAt`/`trunkLive`.
**Consequences**: Pro — one pure, unit-tested decision and one idempotent timer; producers only choose a tier. Con — urgent no longer interrupts (it waits for the next idle); a held delivery with no live trunk must wake the loop to open the trunk first.

### Shutdown drain: flag, hooks, then awaited channel digest

**Choice**: The loop's `finally` sets `shuttingDown = true`, runs every `onShutdown` hook (error-isolated), then `drainQueueToChannel()`, then closes the active trunk (announcing `Wrapping up the conversation…` / `Done` when one is active). `drainQueueToChannel` clears the timer, sorts the remaining queue with `compareQueued`, and renders it as one `buildDigest` straight through `channel.deliver()`.
**Why**: The inbox loop has exited by the `finally`, so a queued item can no longer become an agent turn — rendering the digest straight to the channel is the only way it reaches the user. Holding hook output behind the flag and letting the single awaited drain emit one ordered digest keeps one exit path the process waits on.
**Consequences**: Pro — notices queued or pushed at shutdown reach the user as one digest. Con — the shutdown digest is a plain channel render, not an agent turn.

### Crash drain: route uncaught errors through the same abort path

**Choice**: A `ShutdownController` (`src/shutdown.ts`) routes `SIGINT`, `SIGTERM`, `uncaughtException`, and `unhandledRejection` through one idempotent trigger. The first call aborts the `AbortController` the loop drains on; a crash cause additionally arms an unref'd force-exit timer. A second trigger force-exits immediately and clears the timer. `app.ts` sets `process.exitCode = 1` once a crash drain completes so the loop empties and pino flushes.
**Why**: Post-processing reads the durable on-disk transcript and is error-isolated per step, so salvaging it on the crash path is safe — the same `finally` block trusted on graceful shutdown. Without this, an unhandled rejection exits before the drain, deferring the whole trunk's memory to next-startup recovery.
**Alternatives Considered**: Minimal cleanup + immediate exit (defers all work to recovery — the fragility this closes).
**Consequences**: Pro — a crash mid-conversation still extracts memory, updates context, and commits before exit. Con — on the unstable `uncaughtException` path the pino flush is best-effort.

## System Behavior

### Scenario: User redirects a long run mid-exchange

**Given**: The agent is generating a response in the active trunk
**When**: The user submits "actually, focus on the second file" (no `/queue` prefix)
**Then**: `submit()` sees `exchanging` with an active non-system trunk and calls `session.steer(prompt)` — the input joins the running generation without starting a new exchange; a `/queue `-prefixed message instead waits for the next exchange.

### Scenario: Restart mid-conversation resumes the current branch

**Given**: An active same-day trunk whose live branch was extended after a collapse
**When**: The process restarts and the next exchange runs `ensureTrunk`
**Then**: The trunk reopens and the leaf is re-seated onto the current base, so the conversation continues on the current branch rather than extending a stale collapsed branch.

### Scenario: Bare `/new` prompts, then collapses on the argument

**Given**: The user sends a bare `/new`
**When**: `submit()` runs the pending-input intercept
**Then**: A non-LLM prompt ("What's the first message for the new topic?") is rendered and a pending state is set; the next message is captured as the argument and re-dispatched as `/new <arg>`, which force-collapses via the boundary. A different slash command cancels the pending state; the TTL or a restart clears it.

### Scenario: Rollback replays the triggering message

**Given**: `/rollback` has rewound the tree and applied the opposite transition
**When**: the boundary calls `replay(text, header)`
**Then**: A synthetic system-origin message is enqueued at the front of the inbox, bypassing `submit()`, and runs as the next serial turn — re-answering the triggering message under the corrected framing, with the rollback header on its response.

### Scenario: Nightly close, then recovery after downtime

**Given**: A trunk left open when the nightly cron was skipped (downtime)
**When**: The next startup runs `recoverStaleTrunks`
**Then**: The trunk is run through the close pipeline and retired, attributed to its own day (derived from its session header, not the recovery day); the per-step markers skip any work a prior partial close already completed.

### Scenario: Background delivery during a conversation

**Given**: An extension calls `app.channels.deliver({ text, tier: "normal" })` while the agent is generating
**When**: The exchange completes and the Normal idle window elapses — or its max-hold expires first — and a trunk is live
**Then**: The queued item, with any others, is injected as one system-origin digest turn the agent surfaces; it never steers the in-flight exchange.

### Scenario: Uncaught error drains before exit

**Given**: An active trunk with pending post-processing, and an unhandled rejection fires
**When**: `ShutdownController` catches the rejection
**Then**: The loop's `finally` drains (post-processing runs) through the same path as a graceful signal, then the process exits non-zero so a supervisor restarts it.

## Notes

- `status(text)` both emits a `status` event on the app event bus and calls the active channel's optional `status()`; **while shutting down** there is no streaming renderer, so `status()` reroutes to the channel's `shutdownStatus()`.
- The shutdown sequence announces itself: with an active trunk, the loop's `finally` awaits `emitShutdownStatus("Wrapping up the conversation…")`, runs `closeTrunk()` (per-processor progress on the same message), then awaits `emitShutdownStatus("Done")`.
- The exchange's `try/finally` guarantees `exchanging` resets, `lastExchangeAt` stamps, held deliveries flush, and the queue re-evaluates even when `respond()` throws.
- A nightly/`closeTrunkIfDue` close suppresses per-processor status lines (no renderer to reclaim them); shutdown and explicit closes still surface them.
