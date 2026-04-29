# Design: Update Checker

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../../feature-specs/distribution/update-checker.md](../../feature-specs/distribution/update-checker.md)
**Status**: Current

## Purpose

This document explains the design rationale for the update subsystem: PyPI version fetching, comparison logic, notification delivery, dedup persistence, upgrade execution, in-place restart, and automatic rollback on failed startup.

## Problem Context

Tachikoma runs as a long-lived process that users interact with via Telegram or REPL. When a new version is published to PyPI, users have no way to discover it without manually checking. This subsystem adds periodic background version checking, user notification, the ability to apply updates, and the ability to restart the process in place — the process replaces itself via `os.execv`, preserving the same terminal and tmux session. Upgrade and restart are split into two MCP tools so the agent can also restart for reasons unrelated to upgrades (loading code installed manually via `uv tool install --force`, clearing stale `importlib.metadata` results in the running MCP server, or any general restart need).

**Constraints:**
- Must integrate with the existing central scheduler (DES-010) — not its own loop
- Must use the existing event bus notification system — not a bespoke delivery mechanism
- Must persist dedup state across restarts via the database (ADR-013)
- Must follow the config pattern for `[updates]` settings (Pydantic, TOML)
- The MCP tool runs inside the SDK's tool execution context, but `os.execv` must happen after full async cleanup
- `uv tool upgrade` does not produce machine-readable output or distinct exit codes for "upgraded" vs "already up to date"
- Editable/development installs are incompatible with `uv tool upgrade` — must be detected and reported
- A bare restart (no upgrade) must not write a rollback marker, otherwise the rollback path could activate spuriously when the running version is unchanged

## Design Overview

A lightweight subsystem composed of:

1. **Version checker** — fetches PyPI metadata, compares versions, decides whether to notify
2. **Scheduled job** — runs the checker at a configurable interval via the central scheduler
3. **Config section** — `[updates]` in TOML with `enabled` and `check_interval`
4. **Bootstrap hook** — creates the `AppStateRepository` and registers the scheduled job when enabled
5. **MCP tools** — `check_updates` for on-demand checks, `apply_update` for applying upgrades, `restart` for triggering an in-place process restart
6. **Upgrade executor** — detects editable installs, runs `uv tool upgrade`, reports structured result
7. **Restart event** — `RestartRequested` event type on the bus, consumed by channels to exit their loops
8. **In-place restart** — after clean shutdown, `os.execv` replaces the process preserving PID and terminal
9. **Rollback on failed startup** — if bootstrap fails after upgrade, automatically reinstalls the previous version and restarts; notifies the user through normal channels

The subsystem is minimal: one package (`src/tachikoma/updates/`) with a tick function, a PyPI fetcher, an upgrade executor, three MCP tools, and a bootstrap hook. It closes over the `AppStateRepository`, `EventBus`, and settings — no new long-lived objects beyond what the scheduler already manages.

## Components

### Implementation Structure

| Layer/Component | Responsibility | Key Decisions |
|-----------------|----------------|---------------|
| `src/tachikoma/updates/checker.py` | PyPI fetch, version comparison, notification logic | `urllib.request` (stdlib), `packaging.version` for PEP 440 |
| `src/tachikoma/updates/hooks.py` | Bootstrap hook: create AppStateRepository | DES-003 pattern |
| `src/tachikoma/updates/tools.py` | MCP tools `check_updates`, `apply_update`, and `restart` | DES-006 factory pattern; accepts `EventBus`, used by `restart` to dispatch `RestartRequested` |
| `src/tachikoma/updates/events.py` | `RestartRequested` event type | Follows bubus `BaseEvent[None]` pattern |
| `src/tachikoma/updates/apply.py` | Upgrade execution: editable detection, subprocess invocation, result reporting | stdlib `subprocess.run`, `importlib.metadata` |
| `src/tachikoma/updates/rollback.py` | Rollback marker, rollback notification, and restart notification lifecycle + version rollback execution | DES-011 marker pattern, `subprocess.run` |
| `src/tachikoma/__main__.py` (`_consume_restart_notification`, `_build_back_online_prompt`) | Read restart-notification marker on startup; honor rollback-precedence and stale-marker semantics; persist a one-shot session task that fires ~30s after startup so the agent can announce "back online" | DES-011 consume-once at the call site; DES-010 session-task pipeline drives delivery |
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
  — Fired by: restart MCP tool via bus.dispatch()
  — Consumed by: REPL, TelegramChannel (in run())
  — Handler behavior: set self._restart_requested = True, then trigger run-loop exit
```

**Integration Points**:
- Tick function → `AppStateRepository` (dedup), `EventBus` (notification), settings (interval, enabled)
- MCP tools → `check_for_update()` for read-only checks, `run_upgrade()` + `write_rollback_marker()` for `apply_update`, `bus.dispatch(RestartRequested())` for `restart`
- `restart` tool → `bus.dispatch(RestartRequested())` → channel event handlers
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

RollbackMarker (frozen dataclass, file-backed)
├─ previous_version: str
├─ target_version: str
└─ timestamp: str          ← ISO 8601

RollbackNotification (frozen dataclass, file-backed)
├─ previous_version: str
├─ failed_version: str
└─ error: str

RestartNotification (frozen dataclass, file-backed)
├─ reason: Literal["update", "manual"]
├─ previous_version: str | None
├─ new_version: str | None
└─ timestamp: str          ← ISO 8601
```

The three file-backed dataclasses (`RollbackMarker`, `RollbackNotification`, `RestartNotification`) are all instances of the **DES-011 cross-restart temp marker file** pattern: each has a write/read/clear helper triple, a `${TMPDIR}/tachikoma-*.json` path, and consume-once semantics enforced at the caller.

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
    → write_rollback_marker(old_version, new_version)
    → return success message instructing the agent to call `restart`
  → if result.already_up_to_date:
    → return informational message
  → if result.error:
    → return error message
```

### MCP tool: restart flow

```
Agent invokes restart tool
  → bus.dispatch(RestartRequested())
  → return "Restarting..." message
```

The `restart` tool is intentionally side-effect-only on the bus — it does not write a rollback marker. A bare restart on the same version must not look like a recent upgrade to the next bootstrap. Markers are written exclusively by `apply_update` on the upgrade-success path.

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
  → restart-notification write (DES-011): fresh read_rollback_marker() classifies
    reason="update" (with versions) vs reason="manual"; write_restart_notification()
  → os.execv(sys.argv[0], sys.argv) replaces the process
```

### Rollback on failed startup flow

```
New process starts after update
  → read_rollback_marker() finds pending marker
  → bootstrap.run() raises BootstrapError
    → rollback path activates
    → run_rollback(previous_version) via uv tool install tachikoma-agent==PREV_VERSION
      → success: write_rollback_notification(), clear_rollback_marker(),
                 clear_restart_notification() (drop stale "back online" marker), os.execv
      → failure: clear_rollback_marker(), clear_restart_notification(),
                 print error to stderr, sys.exit(1)
```

### Rollback notification flow

```
Old process starts after rollback
  → read_rollback_marker() returns None (cleared during rollback)
  → read_rollback_notification() finds notification marker
  → bootstrap.run() succeeds (old version works)
  → EventBus created
  → dispatch_notification(bus, source="Update Rollback", severity="error")
  → clear_rollback_notification()
  → _consume_restart_notification(rollback_was_dispatched=True): clear restart marker,
    do NOT schedule a back-online task — rollback wins (R7/AC7)
  → normal startup continues
```

### Restart notification flow

```
New process starts after a `restart`-tool restart
  → read_restart_notification() returns the marker (or None if absent / malformed)
  → bootstrap.run() succeeds
  → clear_restart_notification() unconditionally (consume-once; idempotent)
  → if rollback_notification was dispatched (precedence) → return
  → if notification is None → return
  → otherwise: build prompt from {reason, previous_version, new_version}
  → task_repository.create_definition(TaskDefinition(
      task_type="session", schedule=once at now+30s, prompt=...))
  → instance_generator_tick (60s) creates a pending instance
  → session_task_scheduler_tick enqueues the BufferedItem at NORMAL priority
  → buffer delivers when channel reattach + idle/max-hold conditions met
  → agent renders one short "back online" message via the active channel
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

**Choice**: Add a `# Updates` section to the `SYSTEM_PREAMBLE_TEMPLATE` string in `context/loading.py`, placed between the existing `# Detached Processes` section and `# Context Documents`. The section documents all three update tools (`check_updates`, `apply_update`, `restart`) and explicitly describes the upgrade → restart two-step flow so the agent does not assume `apply_update` restarts on its own.

**Why**: All other tool capabilities (tasks, workflows, projects, git, detached processes) are documented in the preamble. Adding update tools there is consistent and ensures the agent always knows about them. The preamble is rendered once at startup and included in every session's system prompt. Documenting the two-step flow in the preamble (in addition to each tool's own description) gives the agent reinforced guidance — the per-tool descriptions tell it what each tool does, and the preamble tells it how to compose them.

**Alternatives Considered**:
- **Separate context entry via context provider**: Would require a new `ContextProvider` implementation and registration in the pre-processing pipeline. Overkill for a static description.
- **Injected by the updates hook during bootstrap**: Would couple the updates subsystem to the context assembly process. The preamble template is the canonical place for tool documentation.
- **Rely solely on per-tool descriptions**: Would leave the upgrade → restart sequencing implicit. The preamble makes it explicit so the agent cannot mis-sequence.

**Consequences**:
- Pro: Consistent with existing pattern (tasks, git, workflows all documented in preamble)
- Pro: Always included — no conditional loading or provider registration needed
- Pro: The two-step flow is documented in the canonical place the agent reads first
- Con: Preamble grows slightly (negligible — ~8 lines)

### Cross-restart markers: DES-011

**Choice**: All three cross-restart bridges (`tachikoma-update-pending.json`, `tachikoma-update-rollback.json`, `tachikoma-restart-notification.json`) use the **DES-011 cross-restart temp marker file pattern**: a `${TMPDIR}/tachikoma-*.json` path, frozen-dataclass payload, write/read/clear helper triple in `rollback.py`, and consume-once semantics enforced at the call site.

See `docs/design/DES-011-cross-restart-temp-marker-files.md` for the rationale, alternatives considered (ADR-013 `app_state`, env vars, workspace dir), and the patterns the helpers must follow.

### Restart notification: session task instead of notification-bus dispatch

**Choice**: When the previous run wrote a restart-notification marker, schedule a one-shot `TaskDefinition` (type=`session`, `at = now + 30s`) rather than dispatching a `Notification` event on the bus the way the rollback notification does.

**Why**: The rollback notification is a fixed text message and the rollback path simply needs delivery. The restart notification needs the agent to *generate text* — a "back online" sentence that may include version context. Session tasks drive the agent through the existing prompt pipeline (channel → coordinator → SDK), which the notification system does not. Persisting a `TaskDefinition` (instead of a direct `BufferedItem.enqueue`) also gives consume-once durability across an unexpected second restart: a crash between marker-clear and task-fire still surfaces the notification on the run that finally stays up.

**Alternatives Considered**:
- **`Notification` event via `dispatch_notification`**: simpler, but produces fixed text only — cannot inject the agent's voice or have it phrase the version transition naturally
- **Direct `buffer.enqueue(BufferedItem)`**: bypasses persistence; lost on a second restart before delivery
- **Synchronous channel render at startup**: the channel isn't connected yet, and the user expects the agent's voice, not a system-printed line

**Consequences**:
- Pro: Agent renders a natural one-line message via the active channel
- Pro: Persisted definition survives a second restart between marker-clear and task-fire
- Pro: Reuses the existing scheduler pipeline; "no channel attached yet" is handled by the buffer
- Con: ~30s delay before the user sees the message (acceptable for an informational signal)

### Restart notification 30-second `at` offset

**Choice**: The one-shot session task is scheduled for `now + 30s`, not `now`.

**Why**: Lets the active channel finish reattaching (Telegram polling, REPL prompt) before delivery, while staying short enough that the user still associates the message with the restart they just witnessed. The instance generator runs every 60s, so actual user-visible delivery may be slightly later because of the buffer's normal-priority idle gating — acceptable for an informational message.

**Alternatives Considered**:
- **Fire immediately (`now`)**: risks dispatch before the buffer subscribes its handlers, or before the channel is ready to render
- **Longer offset (e.g. 5 minutes)**: defeats the "I just restarted" framing the user expects

### Restart-notification version source: fresh disk read at execv time

**Choice**: Just before `os.execv`, the write path calls `read_rollback_marker()` fresh from disk to decide `reason="update"` vs `reason="manual"`.

**Why**: The pending rollback marker is written by `apply_update` and cleared at line 198 of `__main__.py` after a post-update bootstrap succeeds. The two interesting cases:
- **Case A — clean run, `apply_update` + `restart` mid-session**: line-132 `rollback_marker` was None, line-198 was a no-op, mid-session `apply_update` wrote the marker, fresh read at execv sees it → `reason="update"`.
- **Case B — post-update boot, then later manual restart**: line-132 read the marker, line-198 cleared it, fresh read at execv returns None → `reason="manual"` (correct: the user already saw the upgrade acknowledged in Case A's run).

Using the in-memory `rollback_marker` from line 132 would misclassify Case B as "update". The fresh-read invariant — "is there an `apply_update` outcome that has not yet been confirmed by a successful boot?" — is the right one.

### Rollback scope: BootstrapError only

**Choice**: Only `BootstrapError` from `bootstrap.run()` triggers automatic rollback.

**Why**: The bootstrap system wraps all hook exceptions in `BootstrapError`. These failures are most likely version-specific (incompatible config, broken database migration, missing module). Runtime errors after bootstrap (SDK connection failures, coordinator errors) are not version-specific and should not trigger rollback.

**Consequences**:
- Pro: Scopes rollback to version-incompatibility issues
- Pro: Avoids false rollbacks from transient runtime errors
- Con: A new version that crashes during normal operation (not bootstrap) is not covered

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

### Scenario: Successful upgrade followed by restart

**Given**: A newer version is available on PyPI, the install is a tool install (not editable)
**When**: The agent invokes `apply_update`, then (after seeing the success message) invokes `restart`
**Then**:
1. `apply_update` runs:
   - Editable check passes
   - Current version recorded from `importlib.metadata`
   - `uv tool upgrade tachikoma-agent` runs via `subprocess.run` (timeout: 120s)
   - Exit code 0, version changed in metadata
   - Rollback marker written to temp dir with previous and target versions
   - Tool returns success message with version transition and the instruction to call `restart`
2. Agent generates a response acknowledging the upgrade
3. Agent invokes `restart`:
   - `bus.dispatch(RestartRequested())` fires
   - Tool returns "Restarting..." message
4. Agent finishes generating its response, output is rendered to the user
5. Channel detects `restart_requested` flag and exits run loop
6. `channel.run()` returns, main loop captures flag
7. `Coordinator.__aexit__` runs: cancel idle PP, await pending tasks, close session
8. Main `finally` block: buffer stop, scheduler cancel, background runner shutdown, bus stop, DB close
9. `os.execv(sys.argv[0], sys.argv)` replaces the process

**Rationale**: Splitting upgrade and restart lets the agent decide when to actually swap the process — for example, after warning the user. The rollback marker is still written by `apply_update` (the only step that knows an upgrade just happened), so when the next bootstrap runs it can fall back to the previous version if the new one fails to start.

### Scenario: Restart without upgrade

**Given**: The user installed a new build manually (e.g., `uv tool install --force`) or an MCP server is serving stale `importlib.metadata` results, but no `apply_update` was run in the current process
**When**: The agent invokes `restart`
**Then**: `bus.dispatch(RestartRequested())` fires. Tool returns "Restarting...". The shutdown and `os.execv` flow runs identically to the post-upgrade case. **No rollback marker is written**, so the next bootstrap does not enter the rollback path even if it fails — the new code came from a manual install, not from `apply_update`.

**Rationale**: Restart is a general-purpose mechanism for picking up code or clearing module/version caches. Coupling rollback markers to `apply_update` (and only `apply_update`) keeps the rollback path narrowly scoped to upgrades the system itself performed.

### Scenario: Already up to date

**Given**: The installed version matches the latest on PyPI
**When**: The `apply_update` tool is invoked
**Then**: Tool runs `uv tool upgrade` (exit code 0). Version metadata unchanged. Tool returns informational message with current version. No rollback marker written. Process continues normally — the agent has no reason to call `restart`.

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

### Scenario: Successful upgrade with rollback protection

**Given**: A newer version is applied, rollback marker written
**When**: The new version starts and `bootstrap.run()` completes successfully
**Then**: The pending marker is detected before bootstrap. After bootstrap succeeds, the marker is cleared and a log entry confirms the update. Normal operation continues.
**Rationale**: The happy path should be transparent — the marker is a safety net that's cleaned up when not needed.

### Scenario: Upgrade breaks bootstrap — automatic rollback

**Given**: A newer version is applied, rollback marker written, new version has incompatible config
**When**: The new version starts and `bootstrap.run()` raises `BootstrapError`
**Then**:
1. Rollback marker detected before bootstrap
2. Bootstrap fails, `except BootstrapError` handler catches the error
3. `run_rollback(previous_version)` executes `uv tool install tachikoma-agent==PREV_VERSION`
4. On rollback success: rollback notification marker written, pending marker cleared, `os.execv` restarts with old version
5. On next startup (old version): no pending marker, but notification marker exists
6. Bootstrap succeeds, event bus created, notification dispatched ("Update from X to Y failed and was rolled back")
7. Notification marker cleared, normal operation continues
**Rationale**: The user should never need to manually downgrade. Automatic rollback handles the common failure case, and the notification explains what happened.

### Scenario: Rollback itself fails

**Given**: A newer version broke bootstrap, rollback marker exists
**When**: `uv tool install tachikoma-agent==PREV_VERSION` fails (uv not found, version yanked, network error)
**Then**: Pending marker cleared, restart-notification marker cleared (defense-in-depth: drop any stale "back online" marker from a previous successful update), error logged to stderr ("Rollback failed. Manual intervention required."), process exits with code 1.
**Rationale**: A single rollback attempt prevents infinite restart loops. If rollback fails, the user needs to intervene manually.

### Scenario: Restart notification on second startup (manual restart)

**Given**: User invokes `restart` with no `apply_update` in this session
**When**: The channel exits, the `finally` block runs, and `os.execv` replaces the process
**Then**:
1. Just before `os.execv`: `read_rollback_marker()` returns None → `write_restart_notification(reason="manual", ...)` writes `${TMPDIR}/tachikoma-restart-notification.json`
2. New process: `read_restart_notification()` returns the marker → bootstrap succeeds → `clear_restart_notification()` first → `task_repository.create_definition(TaskDefinition(task_type="session", schedule=once at now+30s, prompt=...))`
3. Within ~90s (instance-generator + buffer normal-priority gating), the agent renders one short "back online" message via the active channel; no version info appears

**Rationale**: User just witnessed the restart and expects acknowledgement. Persisting a session task lets the agent generate the message in its own voice while reusing the existing buffer/idle pipeline.

### Scenario: Restart notification on second startup (update restart)

**Given**: User invokes `apply_update` (writes rollback marker prev=X, target=Y), then invokes `restart`
**When**: `os.execv` is reached, the new process boots cleanly
**Then**:
1. Just before `os.execv`: `read_rollback_marker()` returns the marker → `write_restart_notification(reason="update", previous_version=X, new_version=Y, ...)`
2. New process: bootstrap consumes the rollback marker (line 198 clear), then `_consume_restart_notification` clears the restart marker first and persists a session task with a prompt containing "upgraded from X to Y"
3. The agent renders a back-online message that mentions the version transition

**Rationale**: The user just confirmed an update; the agent's first message after restart reinforces what just happened with the actual version numbers.

### Scenario: Both rollback notification and restart notification present (rollback wins)

**Given**: A previous run successfully applied an update + restart (writing a restart-notification marker), the new version then failed bootstrap, rollback ran successfully (writing a rollback-notification marker)
**When**: The recovered version starts up
**Then**:
1. The rollback-success branch already cleared the restart-notification marker before its `os.execv` (defense-in-depth at lines 260)
2. Even if a stale restart-notification marker had survived, `_consume_restart_notification(rollback_was_dispatched=True)` would clear it without scheduling a session task
3. Only the rollback notification ("Update from X to Y failed and was rolled back to X") is delivered

**Rationale**: One user-facing message per restart. The rollback notification carries a clearer signal ("the update failed"); a contradictory "Back online" announcement would confuse the user.

## Notes

- ETag/If-None-Match caching is deferred — one GET per day is negligible bandwidth
- The `check_updates` MCP tool has zero side effects (no notification, no dedup update) — safe for the agent to call at any time
- `os.execv` preserves PID and terminal session, making it compatible with tmux and systemd without special handling
- The `restart_requested` property is defined on the `Channel` protocol with a default of `False`, so channels that don't support restart simply inherit the default
- Version logging at startup already exists — the new process's version is captured by `importlib.metadata` automatically, so the user can confirm the upgrade from the startup log line
- The 120s subprocess timeout accommodates slow networks and large package downloads while preventing indefinite hangs
- The three cross-restart marker files (`tachikoma-update-pending.json`, `tachikoma-update-rollback.json`, `tachikoma-restart-notification.json`) live in `${TMPDIR}` per DES-011; they are best-effort by design and do not survive a host reboot (acceptable: a reboot ends the cross-restart window)
