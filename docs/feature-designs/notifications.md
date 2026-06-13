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

`index.ts` wires a `NotificationRouter` to the `notify` app event and registers the `notify_user` tool factory. The router parses each payload best-effort (`payload.ts`); urgent notices are formatted and delivered with `gate: "immediate"`, everything else is pushed onto a pending list with an unref'd flush timer. When the flush window elapses, the accumulated notices go out as one delivery — a single formatted notification or a digest (`format.ts`) — with `gate: "idle"` and the configured `maxHoldSeconds`.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/notifications/index.ts` | Wiring: router construction, event subscription, tool registration | Router takes `deliver` as a plain function so tests run without an app |
| `src/extensions/notifications/payload.ts` | Event name constant, severity map, `parseNotifyPayload` | Duck-typed parsing: `text` required, severity/source defaulted — never throws |
| `src/extensions/notifications/router.ts` | Severity routing and flush-window batching | Single pending list + one unref'd `setTimeout`; `flush()` is idempotent and safe to call empty |
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

**Choice**: The router owns a short flush window (default 30 s) that collapses bursts into one message and chooses the digest format; the coordinator's delivery gate owns idle holding and max-hold force flush. The router merely forwards `gate` and `maxHoldSeconds`.
**Why**: Digest formation needs notification semantics (severity, source, item count) that the generic `Delivery` type does not carry; gating needs conversation state (exchange in flight, active session) that extensions cannot see. Splitting at the `Delivery` boundary keeps both sides simple instead of combining both roles in one buffer.
**Alternatives Considered**: Delivering each notice individually and letting the coordinator batch (it has no format knowledge); a full priority-buffer extension with ordering and preemption.
**Consequences**:
- Pro: One digest per burst instead of a message per notice; gate logic stays in one place
- Con: No cross-type priority ordering or preemption — notifications and session tasks are held independently and flushed in arrival order by the coordinator
- Con: Worst-case latency for a non-urgent notice is flush window + idle hold (bounded by `maxHoldSeconds`)

### `notify_user` emits the event instead of delivering directly

**Choice**: The agent tool emits a `NotifyPayload` with `source: "agent"` on the `notify` event rather than calling `app.channels.deliver` itself.
**Why**: Agent-originated notifications get severity routing, batching, and formatting for free, and stay observable to any other `notify` subscriber. The tool remains a thin validator (rejects empty text).
**Consequences**:
- Pro: One code path for all notifications; tests assert on the emitted payload only
- Con: An agent notification during its own exchange waits in the flush window and then the idle gate — it is not appended to the current reply (urgent severity shortcuts this)

## System Behavior

### Scenario: burst of routine notices

**Given**: Two non-urgent payloads (`info` from "ci", `warning` from "monitor") arrive within the flush window
**When**: The window elapses
**Then**: One delivery goes out with `gate: "idle"` and `maxHoldSeconds`, formatted as a digest enumerating both items with severity and source.

### Scenario: urgent notice while notices are pending

**Given**: An `info` notice is pending in the flush window
**When**: An `urgent` payload arrives
**Then**: The urgent notice is delivered immediately (`gate: "immediate"`), even mid-exchange; the pending `info` notice flushes later on its own timer as an idle-gated delivery.

### Scenario: tasks status payload on the shared event

**Given**: The tasks extension emits `{ source, instanceId, status, message }` on `notify`
**When**: The router handles it
**Then**: Parsing returns null (no `text` field) and the payload is skipped with a debug log — the user-facing notice for that outcome was already delivered by the tasks extension itself.

## Notes

- Config lives under `[extensions.notifications]`: `flushWindowSeconds` (30), `maxHoldSeconds` (900).
- The `notify_user` tool is bound to conversational sessions via `app.agent.use`; background side runs are bare (no Tachikoma tools), so background task agents currently cannot call it — task outcome notices come from the executor instead ([tasks.md](tasks.md)).
- The flush timer is unref'd so a pending window never keeps the process alive; notices pending at shutdown are dropped (they are ephemeral by design).
