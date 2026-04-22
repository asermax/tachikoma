# Design: Update Checker

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/distribution/update-checker.md](../../feature-specs/distribution/update-checker.md)
**Status**: Current

## Purpose

This document explains the design rationale for the update subsystem: PyPI version fetching, comparison logic, notification delivery, dedup persistence, upgrade execution, and in-place restart.

## Problem Context

Tachikoma runs as a long-lived process that users interact with via Telegram or REPL. When a new version is published to PyPI, users have no way to discover it without manually checking. This subsystem adds periodic background version checking, user notification, and the ability to apply updates in-place — the process replaces itself via `os.execv`, preserving the same terminal and tmux session.

**Constraints:**
- Must integrate with the existing central scheduler (DES-010) — not its own loop
- Must use the existing event bus notification system — not a bespoke delivery mechanism
- Must persist dedup state across restarts via the database (ADR-013)
- Must follow the config pattern for `[updates]` settings (Pydantic, TOML)
- The MCP tool runs inside the SDK's tool execution context, but `os.execv` must happen after full async cleanup
- `uv tool upgrade` does not produce machine-readable output or distinct exit codes for "upgraded" vs "already up to date"
- Editable/development installs are incompatible with `uv tool upgrade` — must be detected and reported

## Design Overview

A lightweight subsystem composed of:

1. **Version checker** — fetches PyPI metadata, compares versions, decides whether to notify
2. **Scheduled job** — runs the checker at a configurable interval via the central scheduler
3. **Config section** — `[updates]` in TOML with `enabled` and `check_interval`
4. **Bootstrap hook** — creates the `AppStateRepository` and registers the scheduled job when enabled
5. **MCP tools** — `check_updates` for on-demand checks, `apply_update` for applying upgrades
6. **Upgrade executor** — detects editable installs, runs `uv tool upgrade`, reports structured result
7. **Restart event** — `RestartRequested` event type on the bus, consumed by channels to exit their loops
8. **In-place restart** — after clean shutdown, `os.execv` replaces the process preserving PID and terminal

The subsystem is minimal: one package (`src/tachikoma/updates/`) with a tick function, a PyPI fetcher, an upgrade executor, two MCP tools, and a bootstrap hook. It closes over the `AppStateRepository`, `EventBus`, and settings — no new long-lived objects beyond what the scheduler already manages.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/updates/checker.py` | PyPI fetch, version comparison, notification logic | `urllib.request` (stdlib), `packaging.version` for PEP 440 |
| `src/tachikoma/updates/hooks.py` | Bootstrap hook: create AppStateRepository | DES-003 pattern |
| `src/tachikoma/updates/tools.py` | MCP tools `check_updates` and `apply_update` | DES-006 factory pattern; accepts `EventBus` for restart signaling |
| `src/tachikoma/updates/events.py` | `RestartRequested` event type | Follows bubus `BaseEvent[None]` pattern |
| `src/tachikoma/updates/apply.py` | Upgrade execution: editable detection, subprocess invocation, result reporting | stdlib `subprocess.run`, `importlib.metadata` |
| `src/tachikoma/updates/__init__.py` | Re-exports public API | tick function, hook, tool factory, run_upgrade, RestartRequested |
| `src/tachikoma/channel.py` | `restart_requested` protocol property on Channel | Protocol property with default `False` |
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

**Upgrade result** (internal, used by apply_update tool handler):
```python
@dataclass(frozen=True)
class UpgradeResult:
    old_version: str
    new_version: str | None
    already_up_to_date: bool
    error: str | None
    changed: bool
```

**Event Contract**:
```
RestartRequested(BaseEvent[None])
  — No payload. Restart is unconditional once fired — the channel doesn't need
    to know why it's exiting, only whether to exit for restart vs normal shutdown.
  — Fired by: apply_update MCP tool via bus.dispatch()
  — Consumed by: REPL, TelegramChannel (in run())
  — Handler behavior: set self._restart_requested = True, then trigger run-loop exit
```

**Integration Points**:
- Tick function → `AppStateRepository` (dedup), `EventBus` (notification), settings (interval, enabled)
- MCP tools → `check_for_update()` for read-only checks, `run_upgrade()` for applying updates
- `apply_update` tool → `bus.dispatch(RestartRequested())` → channel event handlers
- Channel `restart_requested` property → main loop after `channel.run()` returns
- Main loop → `os.execv` after full cleanup
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

UpgradeResult (frozen dataclass)
├─ old_version: str
├─ new_version: str | None
├─ already_up_to_date: bool
├─ error: str | None
└─ changed: bool

RestartRequested(BaseEvent[None])
  — No fields. Signal-only event.

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

### MCP tool: check_updates flow

```
Agent invokes check_updates tool
  → check_for_update()
    → same fetch + compare logic
  → return UpdateCheckResult as structured text
```

### Upgrade execution flow

```
Agent invokes apply_update tool
  → run_upgrade() via asyncio.to_thread()
    → _is_editable_install() check (PEP 610 direct_url.json)
      → if editable: return error result
    → capture old_version via importlib.metadata
    → subprocess.run(["uv", "tool", "upgrade", "tachikoma-agent"], timeout=120s)
      → FileNotFoundError: return "uv not found" error
      → TimeoutExpired: return timeout error
      → non-zero exit: return error with stderr
    → capture new_version via importlib.metadata
    → compare versions → return UpgradeResult
  → if result.changed:
    → bus.dispatch(RestartRequested())
    → return success message with version transition
  → if result.already_up_to_date:
    → return informational message
  → if result.error:
    → return error message
```

### Restart flow

```
RestartRequested event fired
  → Channel event handler sets _restart_requested = True
    → REPL: flag checked at top of while loop, breaks before prompt_async
    → Telegram: stop_polling() called, polling loop exits
  → channel.run() returns
  → main loop captures channel.restart_requested (protocol property)
  → Coordinator.__aexit__: cancel idle PP, await pending tasks, close session
  → finally block: buffer stop, scheduler cancel, background runner shutdown, bus stop, DB close
  → os.execv(sys.argv[0], sys.argv) replaces the process
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

### Restart signaling via event bus + channel attribute

**Choice**: The MCP tool fires a `RestartRequested` event on the shared `EventBus`. Channels subscribe and set a `_restart_requested` attribute on themselves, then exit their run loops. The main loop checks the channel's `restart_requested` protocol property after `channel.run()` returns.

**Why**: The MCP tool runs deep inside the SDK's tool execution context — it cannot call `os.execv` directly (async resources are still live) or cancel the channel's run loop. The event bus provides a clean decoupling: the tool only signals intent, channels decide how to exit, and the main loop decides whether to execv. The channel attribute survives cleanup (unlike the event, which is consumed and gone by the time the bus is stopped in the `finally` block).

**Alternatives Considered**:
- **Module-level flag only**: Simpler, but the flag alone cannot stop the channel's run loop (the REPL blocks on `prompt_async`). The event provides the trigger; the attribute provides the memory.
- **Exception propagation**: Raise a `RestartRequested` exception from the MCP tool. Rejected because it would abort the current message exchange mid-stream.
- **Coordinator mediation**: Have the coordinator detect a flag and initiate channel shutdown. Rejected because the coordinator is a message orchestrator, not a process manager.

**Consequences**:
- Pro: Clean separation of concerns — tool signals, channel exits, main execvs
- Pro: Uses the existing event bus pattern (same as `BufferedDelivery`, `Notification`)
- Con: Both channels need restart awareness (small subscription handler in each)

### os.execv after the finally block

**Choice**: `os.execv(sys.argv[0], sys.argv)` is called after the `finally` block in `run()` completes, as the last thing before the function returns.

**Why**: The `finally` block stops the buffer, cancels scheduler tasks, shuts down the background runner, stops the event bus, and closes the database. All async resources must be released before `os.execv` replaces the process. Placing execv after the finally block guarantees clean shutdown identical to the normal quit flow.

**Alternatives Considered**:
- **execv inside the finally block**: Would skip subsequent cleanup steps if execv runs before them. Fragile ordering.
- **execv before the finally block**: Coordinator cleanup hasn't run yet. Active sessions wouldn't be closed.

**Consequences**:
- Pro: Full clean shutdown guaranteed — identical to pressing 'q' in REPL
- Pro: No resource leaks (all async resources released before process replacement)
- Con: Slightly longer restart time (full cleanup sequence), but necessary for correctness

### Editable install detection via direct_url.json (PEP 610)

**Choice**: Read `direct_url.json` from the distribution metadata via `importlib.metadata.distribution("tachikoma-agent").read_text("direct_url.json")`. If the JSON contains `"dir_info": {"editable": true}`, report that updates are not available for editable installs.

**Why**: `uv tool upgrade` only works for tool installs (`uv tool install`). Editable installs (`uv pip install -e .`) are development setups where the source code is linked directly. Running `uv tool upgrade` on an editable install either fails silently or does nothing useful. Detecting this upfront gives a clear, actionable error message.

**Alternatives Considered**:
- **Try running `uv tool upgrade` and report failure**: Fragile — the command might succeed (doing nothing) or fail with an unclear error. Better to detect the condition explicitly.
- **Check if `sys.argv[0]` points to a `.venv`**: Indirect and unreliable. `direct_url.json` is the standard mechanism per PEP 610.

**Consequences**:
- Pro: Standard, reliable detection mechanism
- Pro: Clear user-facing error with actionable guidance
- Con: Depends on PEP 610 metadata being present (always true for pip/uv installs)

### System prompt injection in SYSTEM_PREAMBLE_TEMPLATE

**Choice**: Add a `# Updates` section to the `SYSTEM_PREAMBLE_TEMPLATE` string in `context/loading.py`, placed between the existing `# Detached Processes` section and `# Context Documents`.

**Why**: All other tool capabilities (tasks, workflows, projects, git, detached processes) are documented in the preamble. Adding update tools there is consistent and ensures the agent always knows about them. The preamble is rendered once at startup and included in every session's system prompt.

**Alternatives Considered**:
- **Separate context entry via context provider**: Would require a new `ContextProvider` implementation and registration in the pre-processing pipeline. Overkill for a static two-line description.
- **Injected by the updates hook during bootstrap**: Would couple the updates subsystem to the context assembly process. The preamble template is the canonical place for tool documentation.

**Consequences**:
- Pro: Consistent with existing pattern (tasks, git, workflows all documented in preamble)
- Pro: Always included — no conditional loading or provider registration needed
- Con: Preamble grows slightly (negligible — ~6 lines)

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

### Scenario: Successful upgrade

**Given**: A newer version is available on PyPI, the install is a tool install (not editable)
**When**: The `apply_update` tool is invoked
**Then**:
1. Editable check passes
2. Current version recorded from `importlib.metadata`
3. `uv tool upgrade tachikoma-agent` runs via `subprocess.run` (timeout: 120s)
4. Exit code 0, version changed in metadata
5. `bus.dispatch(RestartRequested())` fires
6. Tool returns success message with version transition
7. Agent generates response ("Restarting...")
8. Response fully rendered to user
9. Channel detects `restart_requested` flag and exits run loop
10. `channel.run()` returns, main loop captures flag
11. `Coordinator.__aexit__` runs: cancel idle PP, await pending tasks, close session
12. Main `finally` block: buffer stop, scheduler cancel, background runner shutdown, bus stop, DB close
13. `os.execv(sys.argv[0], sys.argv)` replaces the process

**Rationale**: The restart only triggers after the full message exchange completes. The tool fires the event and returns; the SDK finishes generating the agent's response; the channel renders every event; only then does the channel loop check the restart flag. This guarantees the user always sees the restart notification before the process exits.

### Scenario: Already up to date

**Given**: The installed version matches the latest on PyPI
**When**: The `apply_update` tool is invoked
**Then**: Tool runs `uv tool upgrade` (exit code 0). Version metadata unchanged. Tool returns informational message with current version. No restart event fired. Process continues normally.

**Rationale**: Idempotent behavior — calling `apply_update` when already current is safe and informative.

### Scenario: Upgrade failure

**Given**: `uv tool upgrade` fails (network error, permission denied, uv not found)
**When**: The tool returns a non-zero exit code or the subprocess cannot be launched
**Then**: Tool reports the error with details (stderr, or "uv not found" message). No restart event fired. Process continues normally.

**Rationale**: Failures should not disrupt the running process. The agent can relay the error to the user for manual resolution.

### Scenario: Editable/development install

**Given**: Tachikoma is installed via `uv pip install -e .` or similar
**When**: The `apply_update` tool checks `direct_url.json`
**Then**: Tool detects `"editable": true` in `dir_info`, returns error explaining that updates require a tool install. No subprocess runs.

**Rationale**: Prevents confusing failures from running `uv tool upgrade` on an incompatible install type.

### Scenario: Concurrent operations during restart

**Given**: Background tasks are running when restart triggers
**When**: The shutdown sequence begins
**Then**: Background tasks are cancelled as part of the normal shutdown flow (background runner shutdown in `finally` block). This matches existing shutdown behavior for background tasks.

**Rationale**: No special handling needed — the existing cleanup sequence already handles in-flight work correctly.

### Scenario: Telegram reconnection after restart

**Given**: The process restarts during an active Telegram session
**When**: The new process starts and begins polling
**Then**: Telegram delivers queued messages (server-side buffering). The new process processes them normally.

**Rationale**: Telegram's long-polling mechanism queues messages server-side during the restart gap. No special reconnection logic needed.

## Notes

- ETag/If-None-Match caching is deferred — one GET per day is negligible bandwidth
- The `check_updates` MCP tool has zero side effects (no notification, no dedup update) — safe for the agent to call at any time
- `os.execv` preserves PID and terminal session, making it compatible with tmux and systemd without special handling
- The `restart_requested` property is defined on the `Channel` protocol with a default of `False`, so channels that don't support restart simply inherit the default
- Version logging at startup already exists — the new process's version is captured by `importlib.metadata` automatically, so the user can confirm the upgrade from the startup log line
- The 120s subprocess timeout accommodates slow networks and large package downloads while preventing indefinite hangs
