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
- `SessionTaskReady(BaseEvent)`: carries task instance + completion callback — dispatched by session task scheduler, consumed by channels
- `TaskNotification(BaseEvent)`: carries message + severity — dispatched by background executor, consumed by channels
