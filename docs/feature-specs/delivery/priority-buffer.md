# Priority Buffer

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Background-originated items — task notifications from the executor and ready session-task instances from the scheduler — are held in a unified priority buffer until the conversation reaches a natural pause. Three priority levels (Urgent, Normal, Low) determine both queue ordering and how long each item may wait for an idle window before being force-delivered. Items are always delivered as new message turns, never as steering messages injected mid-response.

## User Stories

- As a user, I want background updates to reach me at natural conversation pauses so that routine notifications don't interrupt what I'm doing
- As a user, I want urgent background updates to reach me quickly while low-priority ones wait patiently
- As a user, I want pending background items to be delivered before shutdown so that nothing relevant is silently dropped

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Notifications and session tasks are delivered as new conversation turns, never as steering messages injected mid-response |
| R1 | A unified priority buffer handles both notifications and session tasks, extensible for future item types |
| R2 | Three priority levels (Urgent/Normal/Low) determine both queue ordering and timing parameters |
| R3 | Only the front item's timing is active; subsequent items' idle-window timers start fresh when they reach the front of the queue |
| R4 | When the front item's idle window is satisfied (coordinator not busy, elapsed-since-last-exchange ≥ idle window), it is delivered as a new message turn |
| R5 | When the front item's accumulated front-time exceeds its max-hold period, it is force-delivered at the next coordinator-idle moment; Low priority items are never force-delivered |
| R6 | Items preempted by higher-priority arrivals resume with a fresh idle-window countdown, but their max-hold counter accumulates across preemptions |
| R7 | The buffer is event-driven — it wakes on enqueue, coordinator busy→idle transitions, and per-cycle timers computed from the next actionable moment (no interval polling) |
| R8 | On graceful shutdown, pending items are flushed as a single combined digest message with a preamble so the agent summarizes them rather than acting on each; a second SIGINT during flush cancels and forces exit |
| R9 | Buffered items are not persisted across restarts — session-task instances remain pending in the database and are re-detected by the scheduler; notifications are ephemeral |

**Priority timing parameters:**

| Priority | Label | Idle Window | Max Hold |
|----------|-------|-------------|----------|
| 1 | Urgent | 30s | 2 min |
| 2 | Normal | 2 min | 15 min |
| 3 | Low | 5 min | Never force-delivered |

**Queue ordering:** Urgent items always at front (FIFO within tier), Normal notifications and session tasks interleave by arrival order in the middle tier, Low items always at back (FIFO within tier).

## Behaviors

### New-Turn Delivery Guarantee (R0, R4)

All buffered items reach the user as standalone conversation turns through the coordinator, never as steering messages appended to an in-flight exchange.

**Acceptance Criteria**:
- Given an item is buffered, when delivery conditions are met, then the buffer dispatches a `BufferedDelivery` event that the active channel routes through `coordinator.send_message()` as a new turn
- Given the coordinator is mid-response when the buffer determines an item is ready, then delivery waits for the next busy→idle transition before dispatching

### Unified Buffer (R1, R9)

A single buffer component accepts both notifications and session tasks through a common `BufferedItem` model.

**Acceptance Criteria**:
- Given the buffer is running, when a `Notification` event is dispatched on the bus, then the buffer enqueues it as a `BufferedItem` with the event's priority
- Given the buffer is running, when a session task becomes ready, then the scheduler calls `buffer.enqueue()` directly with a `BufferedItem` at Normal priority
- Given the buffer contains items of different types, when delivery conditions are met, then both types are delivered through the same `BufferedDelivery` event
- Given the buffer is running with no items, then it idles without side effects until an item arrives
- Given the process starts up, then the buffer initializes empty; session-task instances remain pending in the database for re-detection by the scheduler
- Given the buffer's internal loop encounters an error, then the error is logged and the loop continues without affecting the coordinator or channels

### Priority Ordering (R2)

Items are ordered by `(priority, arrival_sequence)` — Urgent always ahead of Normal, Normal ahead of Low, and FIFO within each tier.

**Acceptance Criteria**:
- Given multiple items at different priorities, then Urgent items are always ahead of Normal items, and Normal items are always ahead of Low items
- Given multiple items at the same priority, then they are ordered by arrival time (FIFO within tier)
- Given an Urgent item is enqueued while Normal and Low items are already buffered, then the Urgent item is placed ahead of all Normal and Low items (but behind any existing Urgent items)

### Idle-Gated Delivery (R3, R4)

The front item waits until the coordinator is not busy and the time since the last exchange meets its idle window.

**Acceptance Criteria**:
- Given the front item has an idle window of N seconds, when `coordinator` is not busy and `now - coordinator.last_message_time >= N`, then the item is delivered as a new message turn
- Given the front item's idle window is not yet satisfied, then the item remains buffered and is re-evaluated when something actionable changes (enqueue, coordinator idle, or timer expiry)
- Given `coordinator.last_message_time` is None (no messages exchanged yet), then the idle window is not considered satisfied — items remain buffered until the first exchange establishes a timestamp (max-hold still applies for Urgent and Normal)
- Given the front item is delivered and `coordinator.last_message_time` updates, when the next item reaches the front, then its idle-window countdown starts fresh from that delivery timestamp

### Force-Delivery on Max-Hold (R5)

An Urgent or Normal item whose accumulated front-time exceeds its max-hold period is force-delivered at the next coordinator-idle moment; Low items wait indefinitely.

**Acceptance Criteria**:
- Given the front item's accumulated front-time exceeds its max-hold period, when the coordinator is not busy, then it is force-delivered as a new message turn
- Given the max-hold timer fires while the coordinator is mid-response, then force-delivery waits for the next coordinator busy→idle transition before dispatching (never delivered as a steering message)
- Given the front item has priority Low (max hold = never), then it is never force-delivered — it waits indefinitely for the idle window to be satisfied

### Preemption (R6)

When a higher-priority item arrives, the current front item yields the front position; its accumulated front-time is preserved, but its idle-window countdown restarts when it returns to the front.

**Acceptance Criteria**:
- Given a Normal item has been at the front for T seconds of its idle window, when an Urgent item is enqueued and preempts it, then when the Normal item later returns to the front its idle window restarts fresh, but its max-hold counter carries the accumulated T (and continues accumulating)

### Event-Driven Wake (R7)

The buffer uses an event-driven loop with three wake sources: enqueue, coordinator busy→idle transition (via `CoordinatorIdle` event), and a per-cycle timer scheduled for the earliest of idle-window completion or max-hold expiry.

**Acceptance Criteria**:
- Given the buffer is waiting to deliver the front item, when the coordinator transitions from busy to idle, then the buffer wakes via a `CoordinatorIdle` event and re-evaluates delivery conditions
- Given a new item is enqueued that reaches the front, then the buffer wakes and re-evaluates delivery conditions
- Given the front item has outstanding waits, when neither an enqueue nor a `CoordinatorIdle` event occurs, then the buffer wakes via a single timer scheduled for the earliest actionable moment — no interval polling

### Graceful Shutdown Flush (R8)

On graceful shutdown, pending items are bundled into a single digest prompt with a preamble and dispatched as one final exchange through the coordinator.

**Acceptance Criteria**:
- Given a graceful shutdown signal (SIGTERM or first SIGINT) and pending items, then the buffer builds one combined digest prompt containing a shutdown-dump preamble followed by each item's content in priority/FIFO order, and dispatches it as a single `BufferedDelivery`
- Given the shutdown digest is dispatched, when the channel routes it through the coordinator, then the coordinator processes it as a new message turn
- Given the digest contains session-task items, when the combined exchange completes, then each session task's `on_delivered` callback fires (instances marked completed); notification items have no post-delivery callback
- Given the shutdown flush is running while the coordinator is mid-response, then the flush waits for the current response to complete before dispatching
- Given the shutdown flush is in progress, then no grace window is enforced — it waits as long as the final exchange needs to complete
- Given a second SIGINT arrives while the flush is in progress, then the flush and any in-flight coordinator exchange are cancelled and the process exits immediately (session-task instances cancelled mid-flush remain in `running` state for crash recovery on next start)
- Given the buffer has no pending items at shutdown, then no digest is built and shutdown proceeds immediately

## Requires

Dependencies:
- None

Assumes existing:
- Coordinator `last_message_time` and `is_busy` properties, `enqueue()` + `send_message()` APIs (core-architecture)
- `Notification` event dispatched by the background executor and `send_notification` MCP tool (background-task-execution)
- Session-task scheduler that identifies ready session instances (session-task-execution)
- Event bus for `Notification`, `CoordinatorIdle`, and `BufferedDelivery` dispatch (ADR-009)
- Channels that can subscribe to `BufferedDelivery` and route prompts through the coordinator (terminal-repl, telegram)
