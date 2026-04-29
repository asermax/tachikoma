# Plugins

## Overview

Plugin system for extending Tachikoma with third-party capabilities.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [plugin-loading](plugin-loading.md) | Install, discover, and load plugins that contribute skills | ✓ |

## Related Decisions

- ADR-009: General-Purpose Event Bus via bubus (plugin lifecycle events)
- ADR-011: Structured Metadata on Context Entries (skill_name qualified names)
- DES-003: Subsystem-Owned Bootstrap Hooks (plugins_hook)
- DES-006: SDK MCP Tool Server Factory (plugin MCP tools)
