# Tasks

Implementation approaches for proactive task scheduling and execution.

## Sub-Capabilities

| Capability | Description |
|------------|-------------|
| [task-management](task-management.md) | Models, repository, MCP tools, instance generation |
| [session-task-execution](session-task-execution.md) | Scheduler, idle gating, event bus delivery to channels |
| [background-task-execution](background-task-execution.md) | Executor, evaluator loop, adapted pipeline, notifications |

## Related Decisions

- [ADR-007](../../architecture/ADR-007-persistence-layer.md) — Persistence layer (SQLAlchemy async + aiosqlite)
- [ADR-009](../../architecture/ADR-009-event-bus.md) — General-purpose event bus via bubus
- [DES-003](../../design/DES-003-subsystem-bootstrap-hooks.md) — Subsystem-owned bootstrap hooks
- [DES-005](../../design/DES-005-sdk-query-generator-consumption.md) — SDK query() generator consumption
