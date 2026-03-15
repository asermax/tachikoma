# Agent

## Overview

Design documents for the core agent capabilities.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [core-architecture](core-architecture.md) | Coordinator, event system, and SDK adapter | Current |
| [workspace-bootstrap](workspace-bootstrap.md) | Bootstrap registry, hook pattern, workspace directory creation | Current |
| [core-context](core-architecture.md) | Core context files (SOUL.md, USER.md, AGENTS.md) loaded at startup and appended to system prompt; updates via [core-context-updates](core-context-updates.md) | Current |
| [post-processing-pipeline](post-processing-pipeline.md) | Phased pipeline mechanism, processor interface, shared helpers | Current |
| [workspace-version-tracking](workspace-version-tracking.md) | Git module: bootstrap hook and commit post-processor | Current |
| [pre-processing-pipeline](pre-processing-pipeline.md) | Pre-processing pipeline mechanism, provider interface, context assembly | Current |
| [sessions](sessions.md) | Session model, repository, registry, and crash recovery | Current |
| [skills](skills.md) | Skill registry, agent discovery, directory structure, and coordinator integration | Current |
| [core-context-updates](core-context-updates.md) | Context update processor, pending signals mechanism, MCP tools | Current |
| [boundary-detection](boundary-detection.md) | Boundary detection, per-message pipeline, summary processor, session transitions | Current |

## Related Decisions

- ADR-001 through ADR-005: Dev tooling (uv, ruff, ty, pytest, just)
- ADR-006: Logging library (loguru)
- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- ADR-008: System prompt composition via append
- DES-001: Testing conventions
- DES-002: Logging conventions
- DES-003: Subsystem-owned bootstrap hooks
- DES-004: Prompt-driven forked processor pattern
