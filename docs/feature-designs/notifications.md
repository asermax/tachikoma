# Design: Notifications

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/notifications.md](../feature-specs/notifications.md)
**Status**: Current

## Purpose

This document explains how notifications are routed from producers to the user: the loose event contract, the severity split between immediate and batched delivery, and how digest batching layers on top of the coordinator's delivery gate.

## Problem Context

Multiple extensions need to surface out-of-band information to the user (background task outcomes, future monitors), but pi has no notification concept — `ctx.ui.notify` only works in pi's own TUI/RPC modes ([pi-sdk-notes](../reference/pi-sdk-notes.md)), and user-facing UX flows through Tachikoma's channel layer. The coordinator's delivery gate owns hold/flush timing ([conversation-loop.md](../feature-specs/conversation-loop.md)), so this extension only decides *what* gets delivered and *with which gate*.

**Constraints:**
- Producers must not depend on the notifications extension — emitting an app event must be enough (DES-001 `app.events`)
- The `notify` event name is shared: the tasks extension emits machine-readable status payloads on it that must not reach the user as prose
- Idle gating and max-hold force delivery are coordinator concerns reachable only through `app.channels.deliver`

**Interactions:**
- Tasks extension ([tasks.md](tasks.md)): emits status payloads on `notify` (skipped by design) and delivers its own user-facing notices directly
- Conversation loop / channels ([conversation-loop.md](../feature-specs/conversation-loop.md), [telegram.md](../feature-specs/telegram.md)): all deliveries flow through `app.channels.deliver` and are rendered by the active channel

## Design Overview

`index.ts` wires a `NotificationRouter` to the `notify` app event and registers the `notify_user` tool factory. The router parses each payload best-effort (`payload.ts`), then runs it through an in-memory dedup guard: an identical `(source + text)` notice seen within `dedupTtlSeconds` is dropped before any routing. Surviving urgent notices are formatted and delivered with `gate: "immediate"`, everything else is pushed onto a pending list with an unref'd flush timer. When the flush window elapses, the accumulated notices go out as one delivery — a single formatted notification or a digest (`format.ts`) — with `gate: "idle"` and the configured `maxHoldSeconds`. Every delivery also carries a `priority` mapped from severity (`SEVERITY_PRIORITY`: urgent 3, warning 2, info 1; a digest takes the max over its items), so the coordinator's stable priority sort surfaces urgent notices ahead of lower-severity ones when several flush together.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/notifications/index.ts` | Wiring: router construction, event subscription, tool registration | Router takes `deliver` as a plain function so tests run without an app |
| `src/extensions/notifications/payload.ts` | Event name constant, severity map, `parseNotifyPayload` | Duck-typed parsing: `text` required, severity/source defaulted — never throws |
| `src/extensions/notifications/router.ts` | Dedup guard, severity routing, flush-window batching, severity → delivery priority, shutdown drain | Single pending list + one unref'd `setTimeout`; `flush()` is idempotent and safe to call empty; `flushNow()` is the shutdown entry point (delegates to `flush()`); dedup state is an in-memory `Map<key, firstSeenMs>` pruned opportunistically; `SEVERITY_PRIORITY` const map sets each delivery's `priority` (digest = max over items) |
| `src/extensions/notifications/format.ts` | Single-notification and digest text rendering | Source/time header block prefixes every notice; timestamps rendered in UTC |
| `src/extensions/notifications/tools.ts` | `notify_user` agent tool | Emits the same `notify` event instead of delivering directly |

## Key Decisions

### Loose, duck-typed event contract

**Choice**: The `notify` event accepts `unknown`; `parseNotifyPayload` requires only a non-empty `text` string and defaults everything else, returning null (quiet skip) otherwise.
**Why**: Cross-extension signals stay best-effort — producers should not import notification types to emit, and the same event name carries non-notification status objects (tasks) that must not be rendered to the user. A throwing or strict parser would couple every producer to this extension's schema.
**Alternatives Considered**: A typed event payload enforced at emit time; separate event names for user notifications vs status signals.
**Consequences**:
- Pro: Zero coupling for producers; malformed payloads degrade to debug logs, never crashes
- Pro: The tasks status payloads coexist on the same event without double delivery
- Con: Typos in `text`/`severity` fail silently (downgrade or skip) rather than loudly

### Two-stage delivery: router batches, coordinator gates

**Choice**: The router owns a short flush window (default 30 s) that collapses bursts into one message, chooses the digest format, and stamps a severity-derived `priority`; the coordinator's delivery gate owns idle holding, max-hold force flush, and the stable priority sort across all held items. The router forwards `gate`, `maxHoldSeconds`, and `priority`.
**Why**: Digest formation needs notification semantics (severity, source, item count) that the generic `Delivery` type does not carry; gating and cross-item ordering need conversation state (exchange in flight, active session) that extensions cannot see. Splitting at the `Delivery` boundary keeps both sides simple — the router maps severity to a numeric `priority`, the coordinator sorts on it — instead of combining both roles in one buffer.
**Alternatives Considered**: Delivering each notice individually and letting the coordinator batch (it has no format knowledge); a full priority-buffer extension with its own ordering and preemption subsystem (rejected — ordering is a stable sort on `Delivery.priority`, not a subsystem).
**Consequences**:
- Pro: One digest per burst instead of a message per notice; gate and ordering logic stay in one place
- Pro: Severity participates in flush ordering — urgent notices lead warnings, which lead info, when held items flush together; cross-type deliveries (e.g. session tasks) order by the same `priority` field
- Con: No preemption — a held lower-priority item already mid-delivery is not interrupted; ordering applies within a flush batch
- Con: Worst-case latency for a non-urgent notice is flush window + idle hold (bounded by `maxHoldSeconds`)

### TTL dedup guard on `(source + text)`

**Choice**: Before routing, the router keys each parsed notice by `source + text` and drops it if an identical key was recorded within `dedupTtlSeconds` (default 60). State is an in-memory `Map` of key → first-seen epoch ms; expired keys are pruned on each new key, and suppression is logged at info. The guard runs ahead of the severity split, so urgent repeats are dropped too.
**Why**: A producer or the agent can re-emit the same notice — e.g. a retry loop after a transient tool/API error — and without a guard each repeat reaches the user. Keying on the user-visible content (`source + text`) catches exactly the storms that matter; `source` alone is always present (defaulted to `unknown`), so the key never collapses to text-only. The window is short so a genuinely recurring condition still re-notifies once it passes.
**Alternatives Considered**: Persisted dedup state (survives restart but needs a store and migration — overkill for an ephemeral guard); hashing the full payload including severity/title (would let a severity-only change slip an otherwise-identical storm through).
**Consequences**:
- Pro: Re-emit/retry loops collapse to a single delivery; zero producer coordination needed
- Con: A legitimately repeated identical notice inside the window is silently dropped (logged, not delivered); state is per-process and reset on restart

### `notify_user` emits the event instead of delivering directly

**Choice**: The agent tool emits a `NotifyPayload` with `source: "agent"` on the `notify` event rather than calling `app.channels.deliver` itself.
**Why**: Agent-originated notifications get severity routing, batching, and formatting for free, and stay observable to any other `notify` subscriber. The tool remains a thin validator (rejects empty text).
**Consequences**:
- Pro: One code path for all notifications; tests assert on the emitted payload only
- Con: An agent notification during its own exchange waits in the flush window and then the idle gate — it is not appended to the current reply (urgent severity shortcuts this)

### Draining pending notices at shutdown

**Choice**: `index.ts` registers `app.onShutdown("flush", () => router.flushNow())`. `flushNow()` simply calls `flush()`, which clears the window timer and emits the accumulated notices as one delivery. The hook runs during the coordinator's shutdown — after it sets `shuttingDown`, before its final awaited delivery flush — so the emitted delivery is held (the shutdown flag forces every fresh delivery to hold) and then drained by that single `await flushDeliveries(true)`.
**Why**: The flush timer is unref'd so it never fires during a clean shutdown, which previously stranded any notice still inside the window. Routing the drain through the coordinator's awaited flush (rather than delivering straight from the hook) keeps a single ordered exit path and lets the loop await the channel write before closing.
**Alternatives Considered**: Delivering directly from the hook (would bypass the loop's ordering/awaiting and could race teardown); persisting pending notices to redeliver next run (overkill for ephemeral window contents).
**Consequences**:
- Pro: notices pending at shutdown reach the user instead of dying with the process
- Con: an urgent notice that arrives *during* shutdown is held rather than sent immediately — it still goes out in the final flush, just not ahead of teardown

## System Behavior

### Scenario: burst of routine notices

**Given**: Two non-urgent payloads (`info` from "ci", `warning` from "monitor") arrive within the flush window
**When**: The window elapses
**Then**: One delivery goes out with `gate: "idle"` and `maxHoldSeconds`, formatted as a digest enumerating both items with severity and source.

### Scenario: urgent notice while notices are pending

**Given**: An `info` notice is pending in the flush window
**When**: An `urgent` payload arrives
**Then**: The urgent notice is delivered immediately (`gate: "immediate"`), even mid-exchange; the pending `info` notice flushes later on its own timer as an idle-gated delivery.

### Scenario: mixed-severity notices flush together

**Given**: An `info` and a `warning` notice are held by the coordinator, then an `urgent` notice is also held (e.g. its immediate path was gated mid-exchange alongside them)
**When**: The coordinator flushes the held batch
**Then**: The urgent delivery (priority 3) is sent first, the warning (2) next, the info (1) last; two items of the same priority keep their arrival order.

### Scenario: producer re-emits the same notice

**Given**: A producer emits `{ source: "monitor", text: "server down" }` and, 30 s later (inside the 60 s window), emits the identical payload again
**When**: The router handles the second emit
**Then**: The repeat is suppressed before routing and logged at info — only the first notice is delivered. After the window elapses, an identical notice is delivered again.

### Scenario: tasks status payload on the shared event

**Given**: The tasks extension emits `{ source, instanceId, status, message }` on `notify`
**When**: The router handles it
**Then**: Parsing returns null (no `text` field) and the payload is skipped with a debug log — the user-facing notice for that outcome was already delivered by the tasks extension itself.

## Notes

- Config lives under `[extensions.notifications]`: `flushWindowSeconds` (30), `maxHoldSeconds` (900), `dedupTtlSeconds` (60).
- The `notify_user` tool is bound via `app.agent.use(..., { background: true })`, so it reaches both conversational sessions and autonomous background task runs — a background task agent can call `notify_user` directly (alongside the executor's own task-outcome notices, see [tasks.md](tasks.md)).
- The flush timer is unref'd so a pending window never keeps the process alive; notices pending at shutdown are drained by an `onShutdown` hook (`router.flushNow()`) into the coordinator's final awaited flush rather than dropped.
