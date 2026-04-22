# Update Checker

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

A scheduled subsystem that periodically checks PyPI for newer versions of tachikoma-agent and notifies the user through their active channel when an update is available. The user sees the current and available versions and can choose to update at their convenience — the actual update application is handled separately. An MCP tool also allows on-demand checks.

## User Stories

- As a Tachikoma operator, I want to be notified when a newer version is available so that I can stay current with bug fixes and features without manually checking
- As a Tachikoma operator, I want to ask the agent whether updates are available so that I can check on demand

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Periodically check PyPI for newer versions of tachikoma-agent |
| R1 | Notify the user via direct notification when a newer version is available |
| R2 | Configurable check frequency with a sensible default (once per day) |
| R3 | Silent when already on the latest version — no notification, no log noise |
| R4 | Only notify once per new version — no repeated notifications for the same available version |
| R5 | Manual trigger: agent can check for updates on demand via MCP tool |

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

## Requires

Dependencies:
- ADR-013: Key-Value Application State Table (dedup persistence)
- DES-003: Subsystem-Owned Bootstrap Hooks (initialization)
- DES-006: SDK MCP Tool Server Factory (on-demand tool)
- DES-010: Central Scheduler (scheduled job)
- Configuration system: `[updates]` section (adds R11 to config-system spec)
