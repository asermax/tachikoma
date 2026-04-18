# Detached Processes

Implementation approaches for supervision of detached OS shell commands.

## Sub-Capabilities

| Capability | Description |
|------------|-------------|
| [process-supervision](process-supervision.md) | Repository, spawn/terminate helpers, hybrid exit watcher, MCP tools, bootstrap hook, preamble |

## Related Decisions

- [ADR-007](../../architecture/ADR-007-persistence-layer.md) — Persistence layer (SQLAlchemy async + aiosqlite)
- [ADR-009](../../architecture/ADR-009-event-bus.md) — General-purpose event bus via bubus
- [DES-003](../../design/DES-003-subsystem-bootstrap-hooks.md) — Subsystem-owned bootstrap hooks
- [DES-006](../../design/DES-006-sdk-mcp-tool-server-factory.md) — SDK MCP tool server factory
