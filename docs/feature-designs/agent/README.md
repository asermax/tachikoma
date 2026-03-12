# Agent

## Overview

Design documents for the core agent capabilities.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [core-architecture](core-architecture.md) | Coordinator, event system, and SDK adapter | Current |
| [workspace-bootstrap](workspace-bootstrap.md) | Bootstrap registry, hook pattern, workspace directory creation | Current |
| [sessions](sessions.md) | Session model, repository, registry, and crash recovery | Current |

## Related Decisions

- ADR-001 through ADR-005: Dev tooling (uv, ruff, ty, pytest, just)
- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-001: Testing conventions
