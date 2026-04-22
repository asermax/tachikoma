# Update Checker

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A subsystem that periodically checks PyPI for newer versions of tachikoma-agent, notifies the user when updates are available, and can apply updates in-place. The user sees the current and available versions and can ask the agent to apply the update directly — the process restarts itself, preserving the same terminal and tmux session. Both checking and applying are exposed as MCP tools.

## User Stories

- As a Tachikoma operator, I want to be notified when a newer version is available so that I can stay current with bug fixes and features without manually checking
- As a Tachikoma operator, I want to ask the agent whether updates are available so that I can check on demand
- As a Tachikoma operator, I want the agent to apply an available update and restart automatically so that I can stay current without manually running shell commands or losing my session

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Periodically check PyPI for newer versions of tachikoma-agent |
| R1 | Notify the user via direct notification when a newer version is available |
| R2 | Configurable check frequency with a sensible default (once per day) |
| R3 | Silent when already on the latest version — no notification, no log noise |
| R4 | Only notify once per new version — no repeated notifications for the same available version |
| R5 | Manual trigger: agent can check for updates on demand via MCP tool |
| R6 | Apply a user-confirmed agent upgrade using uv's upgrade mechanism |
| R7 | In-place restart after successful upgrade (same terminal/tmux session via os.execv) |
| R8 | Clean shutdown before restart (matching the graceful quit flow) |
| R9 | Detect and reject editable/development installs with a clear error |
| R10 | Agent awareness of update tools through the system prompt |

## Behaviors

### Periodic version check (R0, R2)

The system queries PyPI at a configurable interval via the central scheduler.

**Acceptance Criteria**:
- Given the scheduler is running, when the configured interval elapses, then the system queries PyPI for the latest version of `tachikoma-agent`
- Given the `[updates]` config section is absent, when the application starts, then update checking is enabled by default with a frequency of once per day (86400 seconds)
- Given the `[updates]` config section specifies a custom interval, when the scheduler runs, then checks occur at that interval
- Given the `[updates]` config section has `enabled = false`, when the application starts, then no scheduled update checker job is registered and no checks occur

### Update notification (R1)

When a newer version is found, a notification is dispatched through the event bus.

**Acceptance Criteria**:
- Given a PyPI check finds a version newer than the currently installed one, when the check completes, then a notification is dispatched through the event bus at Normal priority (Priority.NORMAL)
- Given the notification is delivered, when the user sees it, then it contains the available version and the current version
- Given a notification was dispatched but no channel is active before shutdown, when the application restarts, then the dedup state persists so the version is not re-notified

### Silent on latest (R3)

No noise when the installed version is current.

**Acceptance Criteria**:
- Given a PyPI check finds the installed version matches the latest stable release, when the check completes, then no notification is dispatched
- Given the installed version is higher than the latest stable release (e.g., dev build), when a check completes, then no notification is dispatched
- Given multiple checks occur while already on the latest, when each completes, then no log output beyond debug-level is produced

### One notification per version (R4)

Dedup prevents notification spam.

**Acceptance Criteria**:
- Given a notification was already dispatched for version X, when a subsequent check still finds X as the latest, then no additional notification is dispatched
- Given a notification was dispatched for version X, when a newer version Y becomes available, then a new notification is dispatched for Y
- Given no notification dedup state exists (first run), when a check finds a newer version available, then a notification is dispatched

### Version filtering

Only stable releases trigger notifications.

**Acceptance Criteria**:
- Given the PyPI response contains pre-release or development versions, when comparing versions, then only stable releases are considered for update notification

### Error handling

Network and parse failures are handled gracefully.

**Acceptance Criteria**:
- Given the PyPI API is unreachable, when a check is attempted, then the error is logged at warning level and no notification is dispatched — the next scheduled check retries
- Given the PyPI response is malformed or missing version data, when the response is parsed, then the error is logged at warning level and no notification is dispatched

### Manual trigger (R5)

The agent can check for updates on demand via the `check_updates` MCP tool.

**Acceptance Criteria**:
- Given the agent is asked to check for updates, when the `check_updates` MCP tool is invoked (which takes no parameters), then a version check runs immediately and the result is returned as structured data: current version, latest version, whether an update is available, and whether the latest is a pre-release
- Given the installed package version cannot be determined, when a check runs, then the current version is treated as "0.0.0" and update availability is evaluated against that baseline

### Applying updates (R6, R9)

The agent can apply an available upgrade via the `apply_update` MCP tool.

**Acceptance Criteria**:
- Given the user asks the agent to apply an update, when the `apply_update` tool is invoked, then it runs `uv tool upgrade tachikoma-agent` and returns the result
- Given the upgrade command succeeds and installs a new version, when the tool completes, then it reports the old and new versions and triggers a restart
- Given the installed version is already the latest, when the upgrade command runs, then it reports "already up to date" with the current version and does not restart
- Given the user asks to update without a prior `check_updates` call, when the `apply_update` tool is invoked, then it runs the upgrade regardless (idempotent — no preconditions)
- Given the upgrade command fails (network error, permission denied, uv not found), when the tool completes, then it reports the error and does not restart
- Given the upgrade command fails, when the error is reported, then the running process continues normally without interruption
- Given the package is installed as an editable/development install, when the tool checks install type, then it returns a clear error without attempting the upgrade
- Given the process restarts after a successful upgrade, when the new process starts, then it logs its version at startup so the user can confirm the upgrade succeeded

### In-place restart (R7, R8)

After a successful upgrade, the process replaces itself in-place.

**Acceptance Criteria**:
- Given the upgrade succeeded, when the restart triggers, then the process replaces itself via `os.execv` preserving the same terminal and tmux session
- Given the restart triggers, when the shutdown sequence begins, then the application performs a full graceful shutdown — buffer flush, session close with post-processing, scheduler cancellation, and resource cleanup — matching the behavior of pressing 'q' in the REPL
- Given the user invokes `apply_update` while the agent is processing another message, when the tool runs, then the upgrade proceeds and the restart waits for the current message exchange to finish before shutting down
- Given background tasks are running when the restart triggers, when the shutdown sequence begins, then running background tasks are cancelled (matching existing shutdown behavior)
- Given the restart happens during a Telegram session, when the new process starts, then it reconnects to Telegram automatically and messages sent during the restart gap are processed normally (Telegram queues them server-side)

### Agent awareness (R10)

The agent knows about update management tools.

**Acceptance Criteria**:
- Given the updates subsystem is initialized, when the agent's system prompt is assembled, then it includes a `# Updates` section describing the available update tools (`check_updates` for checking, `apply_update` for applying)

## Requires

Dependencies:
- ADR-009: General-Purpose Event Bus (restart signaling via RestartRequested event)
- ADR-013: Key-Value Application State Table (dedup persistence)
- DES-003: Subsystem-Owned Bootstrap Hooks (initialization)
- DES-006: SDK MCP Tool Server Factory (MCP tools)
- DES-010: Central Scheduler (scheduled job)
- Configuration system: `[updates]` section (adds R11 to config-system spec)
