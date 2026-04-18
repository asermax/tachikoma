# Delivery

Buffered delivery of background-originated items (notifications, session tasks) to the user — holds items until the conversation is at a natural pause.

## Sub-Capabilities

| Capability | Description |
|------------|-------------|
| [priority-buffer](priority-buffer.md) | Unified priority buffer with event-driven idle gating and shutdown-digest flush |

## Related Decisions

- [ADR-009](../../architecture/ADR-009-event-bus.md) — General-purpose event bus via bubus
- [DES-003](../../design/DES-003-subsystem-bootstrap-hooks.md) — Subsystem-owned bootstrap hooks
