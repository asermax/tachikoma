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

`spawn.ts` launches the user command under `sh -c` (optionally wrapped by the limiter) as a detached process-group leader with stdout/stderr appended to per-process files, persists the record, and writes the exit code to an `exit-code` sidecar file from two writers: the spawned shell's EXIT trap — installed by wrapping the command — writes the code before it exits, so the code survives a host restart; and a Node `exit` listener, while the host lives, covers signal deaths (whose trap never fires) and triggers immediate reconciliation. `reconcile.ts` owns the single running → exited transition: it reads the sidecar (one 100ms retry), performs a conditional UPDATE so concurrent reconcilers converge on one winner, and lets only the winner notify — unless the record carries `stop_reason="agent_stopped"`. Three paths feed the reconciler: the periodic watcher sweep (`watcher.ts`), lazy reconciliation inside the tool handlers (`tools.ts`), and crash recovery at bootstrap (`reconcileOnStartup`, notifications suppressed).

Limited processes run inside a *named* transient scope (`tachikoma-<id>.scope`), which makes the scope addressable after spawn. `cgroup.ts` (`SystemctlScopeInspector`) queries that scope through `systemctl --user show`: `MemoryCurrent` for a live usage read surfaced by `query_process`, and `Result` to tell an OOM kill apart from a plain SIGKILL when a process exits with code 137. The scope's own cgroup directory is already gone by reconcile time, but systemd retains the unit's `Result` long enough to attribute the kill; OOM attribution is then stamped onto the record's existing `stop_reason` column as `oom_killed`.

## Components

### Implementation Structure

| Component | Responsibility | Key Decisions |
|-----------|----------------|---------------|
| `src/extensions/detached-processes/index.ts` | `defineExtension` wiring: repository, limiter, scope inspector, bootstrap hook, tool factory, watcher job | Config: `defaultMemoryLimitMb` (1024, `0` disables), `watchIntervalSeconds` (15); the single `SystemctlScopeInspector` is shared by the reconciler and the tools |
| `src/extensions/detached-processes/schema.ts` | `detached_processes` drizzle table, `ProcessStatus` const map, `STOP_REASON_AGENT_STOPPED`, `STOP_REASON_OOM_KILLED` | Status index for the watcher's hot query; `memoryLimitMb` recorded only when actually enforced; OOM attribution reuses the `stop_reason` column (no new column) |
| `src/extensions/detached-processes/repository.ts` | `ProcessRepository` CRUD: `create`, `get`, `listRunning`/`listExited`, `markStopInitiated`, `clearStopReason`, `rename`, `reconcileToExited` | `reconcileToExited` is a conditional UPDATE (`WHERE status='running'`) returning whether the caller won; an optional `stopReason` arg stamps OOM attribution atomically with the transition; `rename` updates only the `name` column (no migration — the column already exists) |
| `src/extensions/detached-processes/spawn.ts` | `spawnProcess` (validation, detach, command wrapping, sidecar listener, persistence, DB-failure cleanup), `terminate` (group signalling + escalation), `isAlive` | `detached: true` makes the child a group leader so `kill(-pid)` reaches the whole tree; parent closes its fd copies after spawn; the command is wrapped with an EXIT trap (`wrapWithExitCapture`) before `limiter.wrap` so the shell self-reports its exit code; the host exit listener is retained as a second writer for signal deaths (whose trap never fires) and to trigger immediate reconciliation; passes the record id to `limiter.wrap` so the scope can be named |
| `src/extensions/detached-processes/limits.ts` | `ProcessLimiter` seam + `SystemdRunLimiter` | `systemd-run --user --scope --unit=tachikoma-<id>.scope` puts the command in a *named* transient cgroup and exits with its status, so liveness and exit codes behave like an unwrapped spawn while leaving the scope addressable |
| `src/extensions/detached-processes/cgroup.ts` | `scopeUnitName`, `ScopeInspector` seam + `SystemctlScopeInspector` (`readMemoryCurrentMb`, `wasOomKilled`) | Reads via `systemctl --user show <unit> -p MemoryCurrent\|Result --value` — agnostic of cgroup nesting and readable post-exit; degrades to null/false off systemd; injectable `show` runner for tests |
| `src/extensions/detached-processes/output.ts` | `readOutputTail` — last 256KB of a log file; `readOutputWindow` — a 0-based `[offset, count)` line slice with total-line count and a past-EOF flag; `readOutputTailMerged`/`readOutputWindowMerged` — read several labeled streams and render each non-empty one as a `[label]` section (the window helper applies the same window to each stream in parallel and reports the longest log's line count) | Generous raw tail window; `truncateTail` trims every read to pi's limits in the tool layer; the window splits on newlines (trailing newline treated as a terminator, not an empty line); merged reads keep stdout and stderr as separated labeled sections rather than blending them (the spawner writes raw stdio with no per-line timestamps, so the streams are concatenated/separated, not interleaved) |
| `src/extensions/detached-processes/watcher.ts` | `createWatcherTick` — sweep running records, reconcile dead ones | Per-record try/catch so one bad record never stops the sweep |
| `src/extensions/detached-processes/reconcile.ts` | `reconcileExit` (shared reconciler), `reconcileOnStartup` (crash recovery) | Single winner notifies; agent-stopped and startup paths suppress notification; a 137 on a limited process consults the scope `Result` for OOM attribution |
| `src/extensions/detached-processes/tools.ts` | Param schemas, six tool handlers, `createProcessToolsFactory` | Handlers are plain functions over a `ProcessToolDeps` bag so tests drive them without pi; `query_process` reads live scope memory for a running limited process and shows OOM stop reasons; `read_process_output` reads **both** stdout and stderr by default (as separated `[stdout]`/`[stderr]` sections via the merged helpers), branching to a windowed read when `offset`/`count` is supplied (applied to each stream in parallel under the default), else tails; `stream="stdout"`/`"stderr"` isolates one; `rename_process` updates the stored name only; `delete_process` removes an exited record (refusing a still-running one); `dispatch_detached_process` rejects a `memory_limit_mb` below 1 or above `os.totalmem()` before spawning |
| `tests/detached-processes/` | Real-spawn integration tests (`sh` children) over an in-memory-style temp DB; `cgroup.test.ts` and `oom.test.ts` cover scope inspection, OOM attribution, and live-usage reporting | `setup.ts` fakes the limiter, logger, notify sink, and scope inspector (an overridable `ScopeInspector` arg to `createTestContext`) |

## Key Decisions

### Self-reporting shell wrapper plus a host-side exit listener

**Choice**: Wrap the user command as `trap '<write $? to sidecar>' EXIT; <command>` so the spawned shell writes its own exit code to `{id}/exit-code` before it exits, and keep the Node `child.on("exit")` listener as a second writer (128 + signal number for signal deaths) and the trigger for immediate reconciliation. `reconcileExit` reads whichever wrote the sidecar.
**Why**: The shell's EXIT trap fires on normal completion *and* on a user `exit N` (with `$? = N`) before the process dies, so the code is on disk even when the host was down at exit time — closing the gap a host-only listener leaves. The trap does not fire when a signal kills the shell, so signal deaths still depend on the host listener; keeping both covers every case except a signal kill that lands while the host is also down. The user command runs in the same shell as the trap (not a subshell), so signal traps the user installs stay on the process-group leader; the trap leaves the shell's exit status equal to the command's, so the host listener writes the same value the trap wrote. Only the sidecar path is quoted (single-quote with `'\''` escaping, applied to the path and again to the whole trap body); the user command passes through verbatim after `EXIT; `.
**Alternatives Considered**: A host-only exit listener writing the sidecar — simple and quote-free, but a process that exits while the host is down records `null` ("unknown"). A subshell wrapper `( <cmd> ); echo $? > id.exit` — also captures codes when the host is down, but isolates the user's signal traps in the subshell so a command that traps SIGTERM no longer makes the leader stubborn (regression, rejected).
**Consequences**:
- Pro: Exit codes survive a host restart for normal exits and explicit `exit N`
- Pro: Signal deaths still get faithful 128+n codes (SIGTERM → 143, asserted in `tests/detached-processes/terminate.test.ts`) via the retained listener, and user signal traps keep working
- Pro: No quoting games around the user's command string — only the sidecar path is quoted
- Con: `ps` shows the wrapper (`sh -c "trap … EXIT; <command>"`) rather than the bare command, though the user command is still visible verbatim
- Con: A signal kill that lands while the host is also down still records `null` (neither writer can run)

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

**Choice**: Memory limits are applied by wrapping the spawn as `systemd-run --user --scope --quiet --unit=tachikoma-<id>.scope -p MemoryMax=<n>M -- sh -c <command>`. The `ProcessLimiter` interface (probe once at bootstrap, `wrap(id, command, limit)` per spawn, `limited` flag in the result) isolates the mechanism. The scope is named after the record id so it can be inspected later.
**Why**: `systemd-run --scope` delegates all cgroup bookkeeping (creation, pid assignment, cleanup) to systemd and stays in the foreground exiting with the command's status, so detection, signalling, and exit codes are unchanged. The seam lets a direct cgroup v2 implementation slot in if needed. Without `--unit` systemd auto-generates an opaque scope name; naming it `tachikoma-<id>.scope` makes the deterministic-from-id unit addressable for usage reads and OOM attribution.
**Consequences**:
- Pro: No cgroup-creation code; graceful degradation is a probe failure plus a warning
- Pro: The record's `memoryLimitMb` reflects reality — stored only when `wrap()` actually limited
- Pro: The named scope unlocks live usage reads and OOM attribution (next decision) without a persisted cgroup-path column
- Con: Linux + systemd user-session only

### Scope inspection via `systemctl --user show`, not direct cgroup-file reads

**Choice**: `cgroup.ts` reads the named scope's `MemoryCurrent` (live usage) and `Result` (OOM vs. plain kill) through `systemctl --user show <unit> -p <prop> --value`, rather than locating and reading `/sys/fs/cgroup/.../memory.current` and `memory.events` directly. The reconciler checks `Result=oom-kill` only when a 137 lands on a limited process, and stamps `stop_reason="oom_killed"` onto the record via the same conditional UPDATE that wins the exit transition.
**Why**: A `systemd-run --scope` unit's cgroup is removed by systemd the moment its process dies, so the post-mortem `memory.events` (`oom_kill` counter) the legacy hand-rolled cgroup relied on is no longer readable at reconcile time — but systemd retains the unit's `Result` for a window after exit, which captures the OOM verdict. `systemctl show` is also agnostic of where the user manager nests its cgroups (no `/proc/self/cgroup` parsing, no mount discovery). Live `memory.current` for a *running* process is readable either way; using `systemctl show` for both keeps one uniform interface behind the `ScopeInspector` seam.
**Alternatives Considered**: Resolving the cgroup path from the scope's `ControlGroup` property and reading `memory.current`/`memory.events` files directly (works while alive, but `memory.events` is gone post-exit); a persisted `cgroup_path` column (rejected — the path is derivable and the file is gone anyway). A new `oom_killed` boolean column (rejected — `stop_reason` already carries exit provenance).
**Consequences**:
- Pro: OOM attribution survives the cgroup's disappearance; no schema migration (reuses `stop_reason`)
- Pro: Injectable `show` runner makes both reads unit-testable without a real systemd session
- Con: Attribution depends on reconciling within systemd's `Result` retention window; a very delayed reconcile of an OOM kill could miss it and fall back to the plain SIGKILL message
- Con: Two extra short-lived `systemctl` invocations per limited-process exit / inspection

### Notification dispatch decoupled through the app event bus

**Choice**: `reconcileExit` calls an injected `notify` callback; `index.ts` binds it to `app.events.emit("notify", payload)`, shaping the payload to the notifications router's contract: `{ title, text, severity, source }`. The reconciler already emits the notifications router's own severity values — `info` (clean exit), `warning` (non-zero, non-OOM exit), or `urgent` (OOM kill) — so `index.ts` passes the severity through unchanged (no remapping). Suppression is record-driven (`stop_reason`) plus explicit `dispatchNotification: false` for terminate and crash-recovery paths.
**Why**: The watcher-side reconciler is the sole notification producer, keeping this extension ignorant of channels and idle gating (DES-001 separation).
**Consequences**:
- Pro: Tests assert notifications on a plain array sink; no channel machinery involved
- Con: Delivery semantics depend entirely on the `"notify"` consumer's payload contract (see Notes)

## System Behavior

### Scenario: Clean exit detected by the watcher

**Given**: A dispatched process finishes with code 0; the spawned shell's EXIT trap wrote `0` to the sidecar (the host listener redundantly writes the same).
**When**: The next `detached-watch` tick runs.
**Then**: Signal-0 fails, `reconcileExit` wins the conditional UPDATE, the record becomes `exited`/`exitCode=0`, and one `"notify"` event with severity `info` is emitted.

### Scenario: Agent stop racing the watcher

**Given**: `terminate_process` set `stop_reason="agent_stopped"` and signalled; the process died just before a watcher tick.
**When**: Both the terminate handler and the watcher call `reconcileExit`.
**Then**: One caller wins the UPDATE; whichever wins, the `stop_reason` check (watcher path) or `dispatchNotification: false` (terminate path) keeps the user from being notified.

### Scenario: Exit while Tachikoma is down

**Given**: A process exits normally after the host was killed; the spawned shell's EXIT trap wrote the code to the sidecar before dying.
**When**: Tachikoma restarts and the `reconcile` bootstrap hook runs.
**Then**: The dead record is reconciled with the recorded exit code (not `null`), no notification fires, and `query_process(archived=true)` shows it on demand. A process that was *signal-killed* while the host was also down has neither a trap that fires nor a live listener, so the sidecar is absent and it reconciles with `exitCode=null` (`readExitCode` returns `null` for a missing or non-numeric sidecar).

### Scenario: Limited process killed by the OOM killer

**Given**: A process with a 256MB limit allocates past it; the kernel OOM-kills its scope and the exit listener records 137 in the sidecar.
**When**: A reconciler runs (watcher, lazy, or crash recovery while the window holds).
**Then**: Because the exit code is 137 and the record carried a limit, `reconcileExit` queries the scope's `Result`, sees `oom-kill`, wins the UPDATE while stamping `stop_reason="oom_killed"`, and emits a notification reading "was killed by the OOM killer (256MB limit)." A plain SIGKILL of the same process (e.g. an external `kill -9`) yields `Result≠oom-kill` and the original "killed by signal (SIGKILL)." message.

### Scenario: Inspecting live memory of a running limited process

**Given**: A running process dispatched with a 128MB limit.
**When**: The agent calls `query_process` with its `process_id`.
**Then**: The handler reads the scope's `MemoryCurrent` via `systemctl --user show`, converts it to MB, and renders both "Memory limit: 128MB" and "Memory usage: NMB". Off systemd (or once the scope is gone) the usage line is simply omitted.

### Scenario: Paging back through earlier output

**Given**: A long-running process whose stdout log has grown well past the tail window.
**When**: The agent calls `read_process_output` with `offset` and `count` (e.g. `offset=200, count=50`).
**Then**: `readOutputWindow` returns the `[200, 250)` line slice (still honoring an explicit `stream`; under the default it applies the window to each stream in parallel and renders the non-empty ones as separated sections), `truncateTail` trims it to pi's limits, and the agent can step the offset to walk the log. A window whose `offset` lands at or past the last line returns a message naming the requested range and the log's total line count (the longest log's count under the default) instead of empty content.

### Scenario: Reading both streams by default

**Given**: A process (e.g. a Python app using `logging`) that writes its normal output to stdout and its logs/errors/progress to stderr.
**When**: The agent calls `read_process_output` with only a `process_id` (no `stream`).
**Then**: The handler returns both streams' tails as separated `[stdout]` and `[stderr]` sections (an empty stream's section omitted) in a single call, trimmed by `truncateTail`. The agent sees the full picture — including the stderr it would otherwise have missed — without having to know which stream the process writes to.

### Scenario: Stubborn process ignoring SIGTERM

**Given**: A process traps SIGTERM (`trap '' TERM`).
**When**: `terminate_process` runs with a short grace period.
**Then**: The group is SIGTERMed, the grace poll expires, SIGKILL is sent to the group, and the exit is reconciled.

## Notes

- Payload shape: the reconciler hands `notify` a `{ source, processId, severity: "info" | "warning" | "urgent", message }` record, and `index.ts` reshapes it into the notifications router's contract — `{ title: "Process <id>", text: message, severity, source }` — passing the severity through unchanged (the `ProcessNotification.severity` type has no `"error"` value, and there is no remapping in `index.ts`). Because the payload carries a non-empty `text` and a valid severity, `parseNotifyPayload` accepts it and the exit notice is queued for the user as an agent turn at the severity-mapped tier (an `urgent` exit — e.g. an OOM kill — leads the queue with the shortest wait, but no longer interrupts an in-flight exchange)
- Tools are registered through `app.agent.use`, so they exist in interactive agent sessions; headless side runs (`bare: true` in `src/agent/manager.ts`) do not receive them
- There is no system-prompt preamble section; tool discoverability relies on `promptSnippet`/`promptGuidelines`
- `tests/detached-processes/setup.ts` still carries a DDL mirror of `schema.ts`; the central migrations (`drizzle/0001_extensions.sql`) already include the table, so the mirror is removable
