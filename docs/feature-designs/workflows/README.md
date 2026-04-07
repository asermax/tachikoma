# Workflows

## Overview

Multi-step workflow execution within skills, tracking progress reliably across context compaction boundaries.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [workflow-state-machine](workflow-state-machine.md) | Workflow definitions, MCP tools, state persistence, and stale cleanup | ✓ |

## Related Decisions

- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-003: Subsystem-owned bootstrap hooks
- DES-004: Prompt-driven forked processor (post-processor base)
- DES-006: SDK MCP tool server factory
