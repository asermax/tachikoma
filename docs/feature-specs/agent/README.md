# Agent

## Overview

Core agent capabilities: the coordinator loop that receives messages, delegates to the Claude Agent SDK, and streams responses as domain events. This is the foundation every other feature builds on.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [core-architecture](core-architecture.md) | Coordinator, event system, and SDK adapter | ✓ |
| [workspace-bootstrap](workspace-bootstrap.md) | Workspace initialization with idempotent hook system | ✓ |
| [sessions](sessions.md) | Persistent conversation session tracking and crash recovery | ✓ |

## Related Decisions

- ADR-001 through ADR-005: Dev tooling (uv, ruff, ty, pytest, just)
- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-001: Testing conventions
