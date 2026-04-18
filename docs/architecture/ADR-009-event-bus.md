# ADR-009: General-Purpose Event Bus via bubus

**Status**: Accepted
**Date**: 2026-03-22

## Context

The task subsystem needs to communicate with channels (session task delivery) and the background executor needs to signal notifications. Future subsystems (memory updates, session lifecycle, proactive triggers) will need the same decoupling. Point-to-point wiring between subsystems doesn't scale — each new producer/consumer pair would need explicit integration.

A general-purpose event bus provides typed, decoupled communication between subsystems without them knowing about each other.

## Decision

Use `bubus.EventBus` as the project-wide event bus for inter-subsystem communication. A single `EventBus` instance is created in `__main__.py` and passed to all subsystems that need it. Events are Pydantic `BaseEvent` subclasses dispatched by class type.

Key characteristics:
- **Typed events**: Subscribe by event class, not string names
- **Async-native**: `dispatch()` and `expect()` are async
- **FIFO ordering**: Events are processed in dispatch order
- **Fan-out**: Multiple subscribers per event type
- **Middleware support**: Logging, WAL persistence available for debugging and reliability

The `EventBus` is created in `__main__.py` (not in a bootstrap hook) — this keeps the bus lifecycle tied to the application rather than the bootstrap sequence.

## Alternatives Considered

- **`asyncio.Queue` per use case**: Simple but doesn't scale — each new producer/consumer pair needs a new queue, no typed events, no fan-out to multiple subscribers
- **blinker**: Battle-tested (Flask/Pallets) but string-based signals only, no typed event dispatch, callback-only (no await pattern)
- **Custom typed event bus**: ~50-80 lines, full control, but would need to reimplement features bubus already provides (FIFO, middleware, expect, history)

## Consequences

**Positive:**
- Any subsystem can publish/subscribe without knowing about others — fully decoupled
- Typed events (Pydantic models) provide compile-time-like safety
- `expect()` enables channels to await specific event types with filtering and timeout
- Future subsystems plug in by defining new event types — no wiring changes

**Negative:**
- Adds `bubus` as a dependency (Pydantic already a project dependency)
- Relatively new library (96 stars) — but code is straightforward, actively maintained, and small enough to vendor if needed

**Current event types:**
- `Notification(BaseEvent)`: carries prompt + severity + priority (Urgent/Normal/Low) — dispatched by background executor (agent-driven via `send_notification` MCP tool, or automatic on failure) and consumed by the priority buffer (see [delivery/priority-buffer](../feature-designs/delivery/priority-buffer.md)).
- `CoordinatorIdle(BaseEvent)`: carries the transition timestamp — dispatched by the coordinator on every busy→idle transition and consumed by the priority buffer to wake and re-evaluate pending deliveries without polling.
- `BufferedDelivery(BaseEvent)`: carries the prompt, the list of underlying `BufferedItem`s, and an `is_shutdown_digest` flag — dispatched by the priority buffer when delivery conditions are met (or during shutdown flush) and consumed by the active channel, which routes the prompt through the coordinator as a new message turn and fires each item's `on_delivered` callback on completion.

**Retired event types:**
- `SessionTaskReady(BaseEvent)`: previously dispatched by the session task scheduler for channel delivery. Replaced by direct `buffer.enqueue()` calls from the scheduler — the priority buffer now owns idle gating and ordering, and channels observe only `BufferedDelivery`.
- `TaskNotification(BaseEvent)`: previously carried prompt + severity for channel delivery. Replaced by `Notification` (see `tachikoma.notifications`).
