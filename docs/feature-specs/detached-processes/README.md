# Detached Processes

Supervision of OS-level shell commands that outlive Tachikoma — the agent spawns long-running workers on the host (e.g. a Zenki worker on a VPS), and later queries their status, reads their logs, or terminates them without needing to SSH in. Tachikoma acts purely as a lightweight process supervisor; spawned processes have no Claude/SDK involvement.

## Sub-Capabilities

| Capability | Description |
|------------|-------------|
| [process-supervision](process-supervision.md) | Spawn, inspect, read logs from, and terminate detached shell commands; proactive exit detection with buffered notifications |

## Related Decisions

- [ADR-007](../../architecture/ADR-007-persistence-layer.md) — Persistence layer (SQLAlchemy async + aiosqlite)
- [ADR-009](../../architecture/ADR-009-event-bus.md) — General-purpose event bus via bubus
- [DES-003](../../design/DES-003-subsystem-bootstrap-hooks.md) — Subsystem-owned bootstrap hooks
- [DES-006](../../design/DES-006-sdk-mcp-tool-server-factory.md) — SDK MCP tool server factory
