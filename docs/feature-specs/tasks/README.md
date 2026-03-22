# Tasks

Proactive task scheduling and execution — the agent creates, schedules, and runs tasks autonomously.

## Sub-Capabilities

| Capability | Description |
|------------|-------------|
| [task-management](task-management.md) | Task definitions, instances, CRUD tools, and scheduled instance generation |
| [session-task-execution](session-task-execution.md) | Idle-gated delivery of session tasks through the active channel |
| [background-task-execution](background-task-execution.md) | Isolated background execution with evaluator loop and notifications |

## Related Decisions

- [ADR-007](../../architecture/ADR-007-persistence-layer.md) — Persistence layer (SQLAlchemy async + aiosqlite)
- [ADR-009](../../architecture/ADR-009-event-bus.md) — General-purpose event bus via bubus
- [DES-003](../../design/DES-003-subsystem-bootstrap-hooks.md) — Subsystem-owned bootstrap hooks
- [DES-005](../../design/DES-005-sdk-query-generator-consumption.md) — SDK query() generator consumption
