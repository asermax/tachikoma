# Agent

## Overview

Design documents for the core agent capabilities.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [core-architecture](core-architecture.md) | Coordinator, event system, and SDK adapter | Current |
| [workspace-bootstrap](workspace-bootstrap.md) | Bootstrap registry, hook pattern, workspace directory creation | Current |
| [core-context](../../feature-specs/agent/core-architecture.md) | Core context files (SOUL.md, USER.md, AGENTS.md) loaded at startup and appended to system prompt | Current |
| [post-processing-pipeline](post-processing-pipeline.md) | Phased pipeline mechanism, processor interface, shared helpers | Current |
| [workspace-version-tracking](workspace-version-tracking.md) | Git module: bootstrap hook and commit post-processor | Current |
| [sessions](sessions.md) | Session model, repository, registry, and crash recovery | Current |
| [core-context-updates](core-context-updates.md) | Context update processor, pending signals mechanism, MCP tools | Current |

## Related Decisions

- ADR-001 through ADR-005: Dev tooling (uv, ruff, ty, pytest, just)
- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-001: Testing conventions
- DES-004: Prompt-driven forked processor pattern
