# Agent

## Overview

Core agent capabilities: the coordinator loop that receives messages, delegates to the Claude Agent SDK, and streams responses as domain events. This is the foundation every other feature builds on.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [core-architecture](core-architecture.md) | Coordinator, event system, and SDK adapter | ✓ |
| [workspace-bootstrap](workspace-bootstrap.md) | Workspace initialization with idempotent hook system | ✓ |
| [post-processing-pipeline](post-processing-pipeline.md) | Phased post-processing pipeline for running processors after session close | ✓ |
| [workspace-version-tracking](workspace-version-tracking.md) | Automatic git version tracking for workspace changes | ✓ |
| [pre-processing-pipeline](pre-processing-pipeline.md) | Pluggable pipeline for context enrichment before message processing | ✓ |
| [sessions](sessions.md) | Persistent conversation session tracking and crash recovery | ✓ |
| [skills](skills.md) | Skill system: directory-based packages, registry, and sub-agent delegation | ✓ |
| [core-context-updates](core-context-updates.md) | Automated context file updates from conversation learnings | ✓ |
| [boundary-detection](boundary-detection.md) | Conversation boundary detection via topic analysis and per-message processing | ✓ |
| [project-management](project-management.md) | External project repository management via git submodules | ✓ |

## Related Decisions

- ADR-001 through ADR-005: Dev tooling (uv, ruff, ty, pytest, just)
- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-001: Testing conventions
- DES-004: Prompt-driven forked processor pattern
