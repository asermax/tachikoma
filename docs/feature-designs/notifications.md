# Design: Notifications

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/notifications.md](../feature-specs/notifications.md)
**Status**: Current

## Purpose

This document explains how notifications are routed from producers to the user: the loose event contract, the severity split between immediate-enqueue and batched delivery, and how digest batching layers on top of the coordinator's priority queue.

## Problem Context

Multiple extensions need to surface out-of-band information to the user (background task outcomes, future monitors), but pi has no notification concept — `ctx.ui.notify` only works in pi's own TUI/RPC modes ([pi-sdk-notes](../reference/pi-sdk-notes.md)), and user-facing UX flows through Tachikoma's channel layer. The coordinator's priority queue owns tier ordering and hold/drain timing ([conversation-loop.md](../feature-specs/conversation-loop.md)), so this extension only decides *what* gets delivered and *at which tier*.

**Constraints:**
- Producers must not depend on the notifications extension — emitting an app event must be enough (DES-001 `app.events`)
- The `notify` event name is shared across producers; parsing is best-effort so a payload that is not a notification (no `text`) is skipped rather than rendered
- Tier ordering and per-tier idle/max-hold timing are coordinator concerns reachable only through `app.channels.deliver`

**Interactions:**
- Tasks extension ([tasks.md](tasks.md)): background-task notices (failures, the stuck/expired sweeps, the `ask_user` pause) emit a `NotifyPayload` on `notify` and flow through this router; successful completion stays silent (the task agent self-reports via `notify_user`)
- Detached-processes and self-update extensions: also emit `NotifyPayload`s on `notify` (process exits, available updates)
- Conversation loop / channels ([conversation-loop.md](../feature-specs/conversation-loop.md), [telegram.md](../feature-specs/telegram.md)): all deliveries flow through `app.channels.deliver` and are rendered by the active channel

## Design Overview

`index.ts` wires a `NotificationRouter` to the `notify` app event and registers the `notify_user` tool factory. The router parses each payload best-effort (`payload.ts`), then runs it through an in-memory dedup guard: an identical `(source + text)` notice seen within `dedupTtlSeconds` is dropped before any routing. Surviving urgent notices skip the flush window and are delivered immediately at the Urgent `tier`; everything else is pushed onto a pending list with an unref'd flush timer. When the flush window elapses, the accumulated notices go out as one delivery — a single formatted notification or a digest (`format.ts`) — at the `tier` of its highest-severity item. Tier is mapped from severity (`SEVERITY_TIER`: urgent → Urgent, warning → Normal, info → Low; a digest takes the highest over its items), so the coordinator's priority queue surfaces urgent notices ahead of lower-tier ones when several drain together. Delivery timing (idle window, max-hold) lives entirely in the coordinator's tier table — the router no longer carries `maxHoldSeconds`.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/notifications/index.ts` | Wiring: router construction, event subscription, tool registration | Router takes `deliver` as a plain function so tests run without an app |
| `src/extensions/notifications/payload.ts` | Event name constant, severity map, `parseNotifyPayload` | Duck-typed parsing: `text` required, severity/source defaulted — never throws |
| `src/extensions/notifications/router.ts` | Dedup guard, severity routing, flush-window batching, severity → delivery tier, shutdown drain | Single pending list + one unref'd `setTimeout`; `flush()` is idempotent and safe to call empty; `flushNow()` is the shutdown entry point (delegates to `flush()`); dedup state is an in-memory `Map<key, firstSeenMs>` pruned opportunistically; `SEVERITY_TIER` const map sets each delivery's `tier` (digest = highest over items) |
| `src/extensions/notifications/format.ts` | Single-notification and digest text rendering | Source/time header block prefixes every notice; timestamps rendered in UTC |
| `src/extensions/notifications/tools.ts` | `notify_user` agent tool | Emits the same `notify` event instead of delivering directly |

## Key Decisions

### Loose, duck-typed event contract

**Choice**: The `notify` event accepts `unknown`; `parseNotifyPayload` requires only a non-empty `text` string and defaults everything else, returning null (quiet skip) otherwise.
**Why**: Cross-extension signals stay best-effort — producers should not import notification types to emit, and a producer that puts a non-notification object on the event (no `text`) should be skipped rather than crash the router. A throwing or strict parser would couple every producer to this extension's schema.
**Alternatives Considered**: A typed event payload enforced at emit time; separate event names per producer.
**Consequences**:
- Pro: Zero coupling for producers; malformed payloads degrade to debug logs, never crashes
- Pro: A producer can share the event without risking a half-formed payload reaching the user as prose
- Con: Typos in `text`/`severity` fail silently (downgrade or skip) rather than loudly

### Two-stage delivery: router batches, coordinator queues

**Choice**: The router owns a short flush window (default 30 s) that collapses bursts into one message, chooses the digest format, and stamps a severity-derived `tier`; the coordinator's priority queue owns tier ordering, per-tier idle/max-hold timing, and agent-turn delivery across all queued items. The router forwards only `text` and `tier`.
**Why**: Digest formation needs notification semantics (severity, source, item count) that the generic `Delivery` type does not carry; tier ordering and timing need conversation state (exchange in flight, last-exchange time) that extensions cannot see. Splitting at the `Delivery` boundary keeps both sides simple — the router maps severity to a `tier`, the coordinator orders and times on it — instead of combining both roles in one buffer.
**Alternatives Considered**: Delivering each notice individually and letting the coordinator batch (it has no format knowledge); the full legacy priority-buffer subsystem with its own ordering and preemption (rejected — the coordinator's tier queue covers it).
**Consequences**:
- Pro: One digest per burst instead of a message per notice; tier and timing logic stay in one place
- Pro: Severity participates in queue ordering — urgent notices lead warnings, which lead info, when items drain together; cross-type deliveries (e.g. session tasks) order by the same `tier` field
- Con: No preemption — ordering applies within a drained batch
- Con: Worst-case latency for a non-urgent notice is flush window + the tier's idle window (bounded by the tier's max-hold)

### TTL dedup guard on `(source + text)`

**Choice**: Before routing, the router keys each parsed notice by `source + text` and drops it if an identical key was recorded within `dedupTtlSeconds` (default 60). State is an in-memory `Map` of key → first-seen epoch ms; expired keys are pruned on each new key, and suppression is logged at info. The guard runs ahead of the severity split, so urgent repeats are dropped too.
**Why**: A producer or the agent can re-emit the same notice — e.g. a retry loop after a transient tool/API error — and without a guard each repeat reaches the user. Keying on the user-visible content (`source + text`) catches exactly the storms that matter; `source` alone is always present (defaulted to `unknown`), so the key never collapses to text-only. The window is short so a genuinely recurring condition still re-notifies once it passes.
**Alternatives Considered**: Persisted dedup state (survives restart but needs a store and migration — overkill for an ephemeral guard); hashing the full payload including severity/title (would let a severity-only change slip an otherwise-identical storm through).
**Consequences**:
- Pro: Re-emit/retry loops collapse to a single delivery; zero producer coordination needed
- Con: A legitimately repeated identical notice inside the window is silently dropped (logged, not delivered); state is per-process and reset on restart

### `notify_user` emits the event instead of delivering directly

**Choice**: The agent tool (bound only into background runs) emits a `NotifyPayload` with `source: "agent"` on the `notify` event rather than calling `app.channels.deliver` itself.
**Why**: Background-agent notifications get severity routing, batching, and formatting for free, and stay observable to any other `notify` subscriber. The tool remains a thin validator (rejects empty text). It is background-only because the main conversational agent surfaces things by replying directly — an out-of-band notification from the live session would just wait in the queue behind its own reply.
**Consequences**:
- Pro: One code path for all notifications; tests assert on the emitted payload only
- Con: A background-task notification reaches the user only at the next conversation idle (flush window + the tier's idle window); urgent only shortens the wait (Urgent tier), it does not interrupt an active exchange

### Draining pending notices at shutdown

**Choice**: `index.ts` registers `app.onShutdown("flush", () => router.flushNow())`. `flushNow()` simply calls `flush()`, which clears the window timer and emits the accumulated notices as one delivery. The hook runs during the coordinator's shutdown — after it sets `shuttingDown`, before its final awaited queue drain — so the emitted delivery is queued (the shutdown flag holds every fresh delivery) and then rendered to the channel by that single `await drainQueueToChannel()`.
**Why**: The flush timer is unref'd so it never fires during a clean shutdown, which previously stranded any notice still inside the window. Routing the drain through the coordinator's awaited shutdown digest (rather than delivering straight from the hook) keeps a single ordered exit path and lets the loop await the channel write before closing.
**Alternatives Considered**: Delivering directly from the hook (would bypass the loop's ordering/awaiting and could race teardown); persisting pending notices to redeliver next run (overkill for ephemeral window contents).
**Consequences**:
- Pro: notices pending at shutdown reach the user instead of dying with the process
- Con: an urgent notice that arrives *during* shutdown is held rather than sent immediately — it still goes out in the final flush, just not ahead of teardown

## System Behavior

### Scenario: burst of routine notices

**Given**: Two non-urgent payloads (`info` from "ci", `warning` from "monitor") arrive within the flush window
**When**: The window elapses
**Then**: One delivery goes out at the Normal tier (the highest among its items), formatted as a digest enumerating both items with severity and source.

### Scenario: urgent notice while notices are pending

**Given**: An `info` notice is pending in the flush window
**When**: An `urgent` payload arrives
**Then**: The urgent notice is queued immediately at the Urgent tier (no flush-window wait); the pending `info` notice flushes later on its own timer at the Low tier. Both still wait for the coordinator's idle window — urgent leads with the shortest one.

### Scenario: mixed-tier notices drain together

**Given**: An `info` (Low) and a `warning` (Normal) notice are queued by the coordinator, then an `urgent` (Urgent) notice is queued alongside them
**When**: The coordinator drains the batch
**Then**: The urgent delivery is injected first, the warning next, the info last; two items of the same tier keep their arrival order.

### Scenario: producer re-emits the same notice

**Given**: A producer emits `{ source: "monitor", text: "server down" }` and, 30 s later (inside the 60 s window), emits the identical payload again
**When**: The router handles the second emit
**Then**: The repeat is suppressed before routing and logged at info — only the first notice is delivered. After the window elapses, an identical notice is delivered again.

### Scenario: a background task fails

**Given**: A background-task run fails (evaluator `error`, max iterations, a thrown error, or a stuck/expired sweep), so the tasks extension emits `{ text: "❌ …", severity: "warning", source: "Background task: …" }` on `notify`
**When**: The router handles it
**Then**: It accumulates over the flush window and goes out at the Normal tier (`warning`), formatted with the source/time header like any other notice — the tasks extension no longer formats or delivers it itself. A successful run emits nothing.

## Notes

- Config lives under `[extensions.notifications]`: `flushWindowSeconds` (30), `dedupTtlSeconds` (60). Delivery timing (idle window, max-hold) is per-tier in the coordinator, not configured here.
- The `notify_user` tool is bound via `app.agent.use(..., { sessionScopes: ["background"] })`, so it reaches **only** autonomous background task runs — a background task agent calls this single canonical `notify_user` directly (the executor no longer defines its own, see [tasks.md](tasks.md)); the main conversation does not bind it.
- The flush timer is unref'd so a pending window never keeps the process alive; notices pending at shutdown are drained by an `onShutdown` hook (`router.flushNow()`) into the coordinator's final awaited drain rather than dropped.
