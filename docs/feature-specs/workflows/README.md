# Workflows

## Overview

Multi-step workflow execution within skills, tracking progress reliably across context compaction boundaries. Workflows map ordered processes to directory trees the agent navigates natively, with MCP tools enforcing state transitions and a database table persisting state.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [workflow-state-machine](workflow-state-machine.md) | Workflow definitions, MCP tools, state persistence, and stale cleanup | ✓ |

## Related Decisions

- ADR-007: Persistence layer (SQLAlchemy 2.0 async)
- DES-003: Subsystem-owned bootstrap hooks
- DES-006: SDK MCP tool server factory
