# Design: Update Checker

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/distribution/update-checker.md](../../feature-specs/distribution/update-checker.md)
**Status**: Current

## Purpose

This document explains the design rationale for the update checker subsystem: PyPI version fetching, comparison logic, notification delivery, and dedup persistence.

## Problem Context

Tachikoma runs as a long-lived process that users interact with via Telegram or REPL. When a new version is published to PyPI, users have no way to discover it without manually checking. This subsystem adds periodic background version checking and user notification.

**Constraints:**
- Must integrate with the existing central scheduler (DES-010) — not its own loop
- Must use the existing event bus notification system — not a bespoke delivery mechanism
- Must persist dedup state across restarts via the database (ADR-013)
- Must follow the config pattern for `[updates]` settings (Pydantic, TOML)

## Design Overview

A lightweight subsystem composed of:

1. **Version checker** — fetches PyPI metadata, compares versions, decides whether to notify
2. **Scheduled job** — runs the checker at a configurable interval via the central scheduler
3. **Config section** — `[updates]` in TOML with `enabled` and `check_interval`
4. **Bootstrap hook** — creates the `AppStateRepository` and registers the scheduled job when enabled
5. **MCP tool** — `check_updates` for on-demand checks by the agent

The subsystem is minimal: one module (`src/tachikoma/updates/`) with a tick function, a PyPI fetcher, an MCP tool, and a bootstrap hook. It closes over the `AppStateRepository`, `EventBus`, and settings — no new long-lived objects beyond what the scheduler already manages.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/updates/checker.py` | PyPI fetch, version comparison, notification logic | `urllib.request` (stdlib), `packaging.version` for PEP 440 |
| `src/tachikoma/updates/hooks.py` | Bootstrap hook: create AppStateRepository | DES-003 pattern |
| `src/tachikoma/updates/tools.py` | MCP tool `check_updates` | DES-006 factory pattern |
| `src/tachikoma/updates/__init__.py` | Re-exports public API | tick function, hook, tool factory |
| `src/tachikoma/app_state.py` | `app_state` table model + repository | ADR-013 |
| `src/tachikoma/config.py` | `UpdatesSettings` in Settings model | Pydantic frozen model, `[updates]` section |

### Cross-Layer Contracts

**Update check result** (internal, used by both tick and MCP tool):
```python
@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: str | None  # None when PyPI unreachable
    update_available: bool
    latest_is_prerelease: bool
```

**Integration Points**:
- Tick function → `AppStateRepository` (dedup), `EventBus` (notification), settings (interval, enabled)
- MCP tool → same `check_for_update()` function, returns structured dict instead of dispatching notification
- Config → `UpdatesSettings` consumed by hook (to decide whether to register) and tick (for interval)

## Modeling

```
UpdatesSettings (Pydantic, frozen)
├─ enabled: bool = True
└─ check_interval: int = 86400

UpdateCheckResult (frozen dataclass)
├─ current_version: str
├─ latest_version: str | None
├─ update_available: bool
└─ latest_is_prerelease: bool

app_state table (SQLAlchemy)
├─ key: str (PK)     ← "updates.last_notified_version"
├─ value: str        ← e.g. "1.45.0"
└─ updated_at: datetime
```

## Data Flow

### Scheduled check flow

```
Scheduler tick
  → check_for_update()
    → urllib.request: GET https://pypi.org/pypi/tachikoma-agent/json
    → parse response: info.version
    → importlib.metadata.version("tachikoma-agent")
    → packaging.version.Version comparison
      → filter prerelease/dev versions
    → compare: latest > current AND latest NOT prerelease?
      → YES: read app_state "updates.last_notified_version"
        → if latest != last_notified:
          → dispatch_notification(bus, ...)
          → app_state.set("updates.last_notified_version", latest)
        → else: skip (already notified)
      → NO: no action, debug log only
```

### MCP tool flow

```
Agent invokes check_updates tool
  → check_for_update()
    → same fetch + compare logic
  → return UpdateCheckResult as structured text
```

## Key Decisions

### HTTP client: stdlib urllib.request over httpx

**Choice**: Use `urllib.request` from the Python stdlib for the PyPI JSON fetch.
**Why**: This is a single periodic HTTP GET to a well-known endpoint — not a high-throughput or connection-pooled use case. Adding `httpx` as a direct dependency for one request every 24 hours is unjustified. `urllib.request` handles HTTPS and custom headers natively. The async tick calls it via `asyncio.to_thread()` to avoid blocking the event loop.
**Alternatives Considered**:
- `httpx`: Full-featured async HTTP client — overkill for single periodic request
- `aiohttp`: Same concern as httpx

**Consequences**:
- Pro: No new dependency
- Pro: Stdlib is always available, no version pinning concerns
- Con: Slightly more verbose than `httpx.get()`, but the fetch function is ~20 lines

### Version comparison: packaging.version.Version

**Choice**: Use `packaging.version.Version` for all version parsing and comparison.
**Why**: `packaging` is already a transitive dependency (via Pydantic). It implements PEP 440 correctly, handles pre-release/dev versions (`is_prerelease`, `is_devrelease`), and supports natural ordering.
**Consequences**:
- Pro: No new dependency (already available)
- Pro: Correct PEP 440 semantics including pre-release filtering

### Dedup persistence: app_state table

See ADR-013 for the full decision rationale.

### Notification delivery: existing event bus at Normal priority

**Choice**: Use `dispatch_notification()` at `Priority.NORMAL` to deliver update notifications.
**Why**: The notification system already handles priority-based delivery, buffering, and channel routing. Update notifications are informational, not urgent — Normal priority is correct.
**Consequences**:
- Pro: Consistent with how other subsystems notify users
- Pro: Leverages existing priority buffer for delivery timing

## System Behavior

### Scenario: New version available

**Given**: Installed version is `1.42.0`, PyPI latest stable is `1.43.0`
**When**: The scheduled tick runs
**Then**: Fetch PyPI metadata → compare versions → update available → read `app_state` → `last_notified_version` is not `1.43.0` → dispatch notification → write `1.43.0` to `app_state`
**Rationale**: One notification per new version (R4), then silence until the next version appears.

### Scenario: Already notified for this version

**Given**: Installed version is `1.42.0`, PyPI latest stable is `1.43.0`, `last_notified_version` is already `1.43.0`
**When**: The scheduled tick runs
**Then**: Fetch → compare → update available → read `app_state` → already `1.43.0` → skip notification, debug log only
**Rationale**: Dedup prevents notification spam across restarts and multiple ticks.

### Scenario: Already on latest version

**Given**: Installed version matches PyPI latest stable
**When**: The scheduled tick runs
**Then**: Fetch → compare → no update available → no notification, debug log only
**Rationale**: Silent when current.

### Scenario: PyPI unreachable

**Given**: Network error or DNS failure
**When**: The scheduled tick runs
**Then**: Catch exception → log at warning level → no notification → next tick retries
**Rationale**: Transient failures are expected; no point in alarming the user.

### Scenario: Manual on-demand check

**Given**: Agent invokes `check_updates` MCP tool
**When**: Any time, regardless of schedule
**Then**: Run the same fetch + compare → return structured result → do NOT dispatch notification or update dedup state
**Rationale**: The tool answers a question; the scheduled job drives a notification. Mixing the two would cause unexpected notifications from agent queries.

## Notes

- ETag/If-None-Match caching is deferred — one GET per day is negligible bandwidth
- The MCP tool has zero side effects (no notification, no dedup update) — safe for the agent to call at any time
- DLT-061 (Apply agent update) will consume update detection but is a separate delta
