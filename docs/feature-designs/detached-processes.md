# Design: Detached Processes

<!-- This design describes the current implementation approach. Updated through delta reconciliation. -->

**Feature Spec**: [../feature-specs/detached-processes.md](../feature-specs/detached-processes.md)
**Status**: Current

## Purpose

Explain how Tachikoma supervises detached OS shell commands on the pi stack: identity and liveness, exit-code capture, the polling watcher, the memory-limit seam, and integration with the extension services from DES-001. Spawned processes have no agent involvement — this subsystem is a lightweight process supervisor.

## Problem Context

The agent needs to start shell commands that survive Tachikoma's own exit, restart, or crash, and later query, read, or stop them. The implementation reaches that capability with Node primitives and one external tool.

**Constraints:**
- Everything ships as an extension (DES-001): persistence via the shared drizzle handle, periodic work via `app.scheduler`, startup via `app.bootstrap`, agent tools via `app.agent.use` factories
- Tools follow the DES-002 conventions: TypeBox `parameters`, `promptSnippet`/`promptGuidelines`, throw-to-error, output trimmed with pi's `truncateTail`
- Running processes must not depend on the host staying alive — no in-process supervision can be the only exit-detection path
- POSIX-only; memory limits are Linux/systemd-only with graceful degradation

**Interactions:**
- Notifications: exit notices are emitted as `"notify"` app events on `app.events`; the [notifications](../feature-specs/notifications.md) extension owns user delivery
- Scheduler: the watcher is a named `app.scheduler.every` job (`detached-watch`)
- Database: `schema.ts` is aggregated into `src/db/schema.ts` and covered by the shared `drizzle/` migrations (`0001_extensions.sql`)
- Workspace: per-process directories live under `app.workspace.dataDir` (`{workspace}/.tachikoma/processes/{id}/`)

## Design Overview

`spawn.ts` launches `sh -c <command>` (optionally wrapped by the limiter) as a detached process-group leader with stdout/stderr appended to per-process files, persists the record, and — while the host lives — holds a Node `exit` listener that writes the exit code to an `exit-code` sidecar file. `reconcile.ts` owns the single running → exited transition: it reads the sidecar (one 100ms retry), performs a conditional UPDATE so concurrent reconcilers converge on one winner, and lets only the winner notify — unless the record carries `stop_reason="agent_stopped"`. Three paths feed the reconciler: the periodic watcher sweep (`watcher.ts`), lazy reconciliation inside the tool handlers (`tools.ts`), and crash recovery at bootstrap (`reconcileOnStartup`, notifications suppressed).

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/detached-processes/index.ts` | `defineExtension` wiring: repository, limiter, bootstrap hook, tool factory, watcher job | Config: `defaultMemoryLimitMb` (1024, `0` disables), `watchIntervalSeconds` (15) |
| `src/extensions/detached-processes/schema.ts` | `detached_processes` drizzle table, `ProcessStatus` const map, `STOP_REASON_AGENT_STOPPED` | Status index for the watcher's hot query; `memoryLimitMb` recorded only when actually enforced |
| `src/extensions/detached-processes/repository.ts` | `ProcessRepository` CRUD: `create`, `get`, `listRunning`/`listExited`, `markStopInitiated`, `clearStopReason`, `reconcileToExited` | `reconcileToExited` is a conditional UPDATE (`WHERE status='running'`) returning whether the caller won |
| `src/extensions/detached-processes/spawn.ts` | `spawnProcess` (validation, detach, sidecar listener, persistence, DB-failure cleanup), `terminate` (group signalling + escalation), `isAlive` | `detached: true` makes the child a group leader so `kill(-pid)` reaches the whole tree; parent closes its fd copies after spawn |
| `src/extensions/detached-processes/limits.ts` | `ProcessLimiter` seam + `SystemdRunLimiter` | `systemd-run --user --scope` puts the command in a transient cgroup and exits with its status, so liveness and exit codes behave like an unwrapped spawn |
| `src/extensions/detached-processes/output.ts` | `readOutputTail` — last 256KB of a log file | Generous raw window; `truncateTail` trims to pi's limits in the tool layer |
| `src/extensions/detached-processes/watcher.ts` | `createWatcherTick` — sweep running records, reconcile dead ones | Per-record try/catch so one bad record never stops the sweep |
| `src/extensions/detached-processes/reconcile.ts` | `reconcileExit` (shared reconciler), `reconcileOnStartup` (crash recovery) | Single winner notifies; agent-stopped and startup paths suppress notification |
| `src/extensions/detached-processes/tools.ts` | Param schemas, four tool handlers, `createProcessToolsFactory` | Handlers are plain functions over a `ProcessToolDeps` bag so tests drive them without pi |
| `tests/detached-processes/` | Real-spawn integration tests (`sh` children) over an in-memory-style temp DB | `setup.ts` fakes only the limiter, logger, and notify sink |

## Key Decisions

### Host-side exit listener writing a sidecar, instead of a shell wrapper

**Choice**: Spawn the user's command directly under `sh -c`; a Node `child.on("exit")` listener in the host writes the exit code (128 + signal number for signal deaths) to `{id}/exit-code`, which `reconcileExit` reads later.
**Why**: Node already reports child exit codes and signals precisely, including kills of the group leader, without rewriting the user's command line. The sidecar keeps the code available to whichever reconciler runs later (watcher, lazy, terminate).
**Alternatives Considered**: Wrapping the command as `sh -c '<cmd>; echo $? > id.exit'`, which also captures codes when the host is down.
**Consequences**:
- Pro: `ps` shows the user's real command; signal deaths get faithful 128+n codes (SIGTERM → 143, asserted in `tests/detached-processes/terminate.test.ts`)
- Pro: No quoting games around the user's command string
- Con: A process that exits while the host is down has no listener; its code is recorded as `null` ("unknown") by crash recovery or the watcher

### Polling-only watcher

**Choice**: One scheduler job (`detached-watch`, every `watchIntervalSeconds`, default 15s) sweeps `listRunning()` and reconciles records whose pid fails the signal-0 check. There is no event-driven file watcher.
**Why**: With the sidecar written in-process, there is no external file event worth watching; the only thing the watcher must catch is "the pid is gone". A single sweep is simpler than a hybrid file-watch + poll setup, and the lazy reconciliation in every tool handler covers the freshness-sensitive case (the agent asking about a record).
**Alternatives Considered**: Subscribing to the in-process `exit` event for immediate reconciliation — rejected as the sole path because it dies with the host; keeping it in addition was not needed at the current notification latency expectations.
**Consequences**:
- Pro: One code path, trivially testable (`createWatcherTick(deps)()` is awaited directly in tests)
- Con: Up to one interval of detection latency for exit notices

### Signal-0 liveness without PID-reuse protection

**Choice**: `isAlive(pid)` is `process.kill(pid, 0)`, with EPERM counted as alive. No OS start-time identity anchor is stored.
**Why**: Node has no portable create-time API without a new dependency, and the failure mode is benign here: a reused pid keeps a stale record "running" until the squatter exits, at which point the watcher reconciles it (with a `null` code). Destructive signalling targets the process *group* (`-pid`), and group ids of detached children are not recycled into unrelated foreground groups in practice.
**Alternatives Considered**: A create-time identity check via `/proc/<pid>/stat` (Linux-only, hand-rolled) or a dependency.
**Consequences**:
- Pro: Zero dependencies, three-line check
- Con: A reused pid can delay exit detection indefinitely and, in the worst case, `terminate_process` could signal an unrelated process group — accepted, documented in `reconcile.ts`

### `systemd-run` scopes behind the `ProcessLimiter` seam

**Choice**: Memory limits are applied by wrapping the spawn as `systemd-run --user --scope --quiet -p MemoryMax=<n>M -- sh -c <command>`. The `ProcessLimiter` interface (probe once at bootstrap, `wrap()` per spawn, `limited` flag in the result) isolates the mechanism.
**Why**: `systemd-run --scope` delegates all cgroup bookkeeping (creation, pid assignment, cleanup) to systemd and stays in the foreground exiting with the command's status, so detection, signalling, and exit codes are unchanged. The seam lets a direct cgroup v2 implementation slot in if needed.
**Consequences**:
- Pro: No cgroup filesystem code; graceful degradation is a probe failure plus a warning
- Pro: The record's `memoryLimitMb` reflects reality — stored only when `wrap()` actually limited
- Con: Linux + systemd user-session only; no OOM-kill attribution or live memory-usage reads

### Notification dispatch decoupled through the app event bus

**Choice**: `reconcileExit` calls an injected `notify` callback; `index.ts` binds it to `app.events.emit("notify", payload)` with severity `info`/`error`. Suppression is record-driven (`stop_reason`) plus explicit `dispatchNotification: false` for terminate and crash-recovery paths.
**Why**: The watcher-side reconciler is the sole notification producer, keeping this extension ignorant of channels and idle gating (DES-001 separation).
**Consequences**:
- Pro: Tests assert notifications on a plain array sink; no channel machinery involved
- Con: Delivery semantics depend entirely on the `"notify"` consumer's payload contract (see Notes)

## System Behavior

### Scenario: Clean exit detected by the watcher

**Given**: A dispatched process finishes with code 0; the exit listener wrote `0` to the sidecar.
**When**: The next `detached-watch` tick runs.
**Then**: Signal-0 fails, `reconcileExit` wins the conditional UPDATE, the record becomes `exited`/`exitCode=0`, and one `"notify"` event with severity `info` is emitted.

### Scenario: Agent stop racing the watcher

**Given**: `terminate_process` set `stop_reason="agent_stopped"` and signalled; the process died just before a watcher tick.
**When**: Both the terminate handler and the watcher call `reconcileExit`.
**Then**: One caller wins the UPDATE; whichever wins, the `stop_reason` check (watcher path) or `dispatchNotification: false` (terminate path) keeps the user from being notified.

### Scenario: Exit while Tachikoma is down

**Given**: A process exits after the host was killed; no sidecar was written.
**When**: Tachikoma restarts and the `reconcile` bootstrap hook runs.
**Then**: The dead record is reconciled with `exitCode=null`, no notification fires, and `query_process(archived=true)` shows it on demand.

### Scenario: Stubborn process ignoring SIGTERM

**Given**: A process traps SIGTERM (`trap '' TERM`).
**When**: `terminate_process` runs with a short grace period.
**Then**: The group is SIGTERMed, the grace poll expires, SIGKILL is sent to the group, and the exit is reconciled.

## Notes

- Payload contract caveat: the watcher emits `{ source, processId, severity: "info" | "error", message }`, while the notifications router (`parseNotifyPayload`) only delivers payloads carrying a `text` field with severity `info|warning|urgent` — exit notices therefore currently reach raw `"notify"` subscribers but are skipped by the user-facing router. Aligning the payload shape is pending
- Tools are registered through `app.agent.use`, so they exist in interactive agent sessions; headless side runs (`bare: true` in `src/agent/manager.ts`) do not receive them
- There is no rename tool and no system-prompt preamble section; tool discoverability relies on `promptSnippet`/`promptGuidelines`
- `tests/detached-processes/setup.ts` still carries a DDL mirror of `schema.ts`; the central migrations (`drizzle/0001_extensions.sql`) already include the table, so the mirror is removable
