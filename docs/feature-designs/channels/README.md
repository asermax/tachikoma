# Channels

## Overview

Design documents for communication interfaces. All channels implement the `Channel` protocol (`src/tachikoma/channel.py`).

## Channel Protocol

The `Channel` protocol (`@runtime_checkable`) defines the interface all channels implement:

- `get_mcp_servers() → dict[str, McpSdkServerConfig]` — Returns channel-specific MCP tool servers (default: `{}`)
- `get_skill_sources() → list[Path]` — Returns paths to channel-provided skill directories (default: `[]`)
- `run(coordinator) → None` — Starts the channel's main loop

Protocol uses explicit subclassing for defaults — channels that inherit from `Channel` get no-op implementations for `get_mcp_servers()` and `get_skill_sources()`. The lifecycle in `__main__.py`: channel is created first (no coordinator), capabilities are extracted and merged into the coordinator's configuration, then `run(coordinator)` starts the channel with the fully configured coordinator.

## Sub-Capabilities

| Capability | Description | Status |
|------------|-------------|--------|
| [terminal-repl](terminal-repl.md) | Interactive terminal REPL for development | Current |
| [telegram](telegram.md) | Telegram bot for production use | Current |

## Related Decisions

- DES-001: Testing conventions
