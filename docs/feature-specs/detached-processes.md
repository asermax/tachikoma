# Detached Processes

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

Agent tools dispatch, inspect, read output from, and terminate OS-level shell commands that run detached from Tachikoma: each process runs in its own process group with stdout/stderr captured to per-process files, so it survives Tachikoma's exit, restart, or crash. Records persist in the shared SQLite database; a polling watcher detects exits and emits `"notify"` app events (see [notifications](./notifications.md)), and a startup bootstrap reconciles records orphaned while the host was down. Memory limits are applied through named `systemd-run` scopes when available; the named scope is also queried (via `systemctl --user show`) for live memory usage and to attribute exit-code-137 deaths to the OOM killer. The extension also contributes a usage context section (scope: main + background) describing when to spawn detached processes and the available tools.

## User Stories

- As a user, I want Tachikoma to start long-running shell commands (servers, builds, downloads) that outlive the conversation and Tachikoma itself, so the agent can run workers on the host
- As a user, I want to ask Tachikoma to check on previously-started processes, read their output, or stop them, without shelling in myself
- As a user, I want to hear about it when a detached process exits, without being told about exits I asked for

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Provide agent tools `dispatch_detached_process`, `query_process`, `read_process_output`, `terminate_process`, `rename_process`, and `delete_process`, registered in every agent session via a pi extension factory (`app.agent.use`) |
| R1 | Hard detach: spawn via `sh -c` as its own process group leader (`detached: true`, unref'd) with stdin ignored and stdout/stderr redirected to files, so processes survive Tachikoma's exit, restart, or crash |
| R2 | Persist records in the `detached_processes` table (id, name, command, cwd, pid, status, exit code, stop reason, stdout/stderr paths, memory limit, started/exited timestamps), indexed by status and covered by the central drizzle migrations |
| R3 | `dispatch_detached_process` accepts `name` (required, non-blank), `command` (required, non-blank shell string), optional `cwd`, `env` overrides, and `memory_limit_mb`; returns the record ID, PID, and both log paths |
| R4 | Capture stdout and stderr to separate append-mode files under `{workspace}/.tachikoma/processes/{id}/`; files remain on disk after exit |
| R5 | Default `cwd` is Tachikoma's own working directory; the spawned environment is the OS environment with `env` entries merged on top |
| R6 | Memory limiting wraps the command in `systemd-run --user --scope --unit=tachikoma-<id>.scope -p MemoryMax=...` when an effective limit resolves (per-call `memory_limit_mb`, else configured `defaultMemoryLimitMb` — default 1024, `0` disables); naming the scope after the record id makes it addressable for live usage reads and OOM attribution. When `systemd-run` is unavailable (probed once at bootstrap) the spawn degrades to a plain shell with a warning, and the record stores a limit only when one was actually enforced |
| R7 | `memory_limit_mb` below 1 is rejected with a validation error before anything is spawned; a `memory_limit_mb` exceeding the host's total system RAM (`os.totalmem()`) is likewise rejected with a validation error naming the system RAM, before anything is spawned |
| R8 | `query_process` with `process_id` returns full record details; without it, lists running records (`archived=true` lists exited ones); dead-but-`running` records encountered on either path are lazily reconciled to `exited` before responding; unknown ids return a "not found" error |
| R8a | For a `running` process that carries a memory limit, `query_process` with `process_id` reads the scope's live `memory.current` (via `systemctl --user show <scope> -p MemoryCurrent`) and reports it in MB alongside the limit; the usage line is omitted when no reading is available (no systemd session, scope gone) |
| R9 | `read_process_output` returns the tail of the captured stdout (default) or stderr (`stream="stderr"`) log, trimmed with pi's `truncateTail` and prefixed with a truncation marker when shortened; missing or empty logs yield "No output yet." |
| R9a | `read_process_output` also supports a windowed read: when `offset` (0-based line, default 0) and/or `count` (lines, default 100) are given, it returns the `[offset, offset + count)` line slice of the selected stream instead of the tail; a window starting at or past EOF returns a message naming the requested range and the log's line count; a negative `offset` or `count` below 1 is rejected with a validation error |
| R9b | `rename_process` updates a record's stored display `name` (rejecting a blank/whitespace name) without touching the running process; unknown ids return a "not found" error |
| R9c | `delete_process` removes an exited process record from the tracked list; it refuses to delete a record whose process is still alive (`status="running"` and pid alive), returning an error telling the user to terminate it first; unknown ids return a "not found" error. The captured log files on disk are not affected |
| R10 | `terminate_process` signals the whole process group (default SIGTERM, signal name validated against the OS signal table), escalates to SIGKILL after `grace_seconds` (default 10); `grace_seconds=0` sends the signal and returns immediately, leaving the exit to the watcher; already-exited records return "already stopped" without signalling |
| R11 | Agent-initiated stop tracking: `stop_reason` is set to `agent_stopped` before signalling and cleared if signal delivery fails with EPERM; exit notifications are suppressed for agent-stopped records |
| R12 | Exit-code capture: the command is wrapped so the spawned shell writes its own exit code to an `exit-code` sidecar file via an EXIT trap before exiting, making the code recoverable even when the host was down at exit time (including a user `exit N`). The host's in-process exit listener is retained as a second writer for signal deaths (128 + signal number — the EXIT trap never fires on a signal) and to trigger immediate reconciliation. Reconciliation reads the sidecar with one 100ms retry; only a signal-killed process whose host was also down (no trap fires, no listener alive) records an unknown (`null`) code |
| R13 | A scheduler job sweeps `running` records every `watchIntervalSeconds` (default 15), reconciling dead ones, with per-record error isolation |
| R14 | Reconciliation transitions running → exited via a conditional UPDATE so concurrent reconcilers converge on a single winner; only the winner dispatches a notification |
| R15 | Exit notifications are emitted as `"notify"` app events carrying the process name, id, and exit code, with severity `info` for exit code 0, `urgent` for an OOM kill (see R15a), and `warning` for any other non-zero exit (the `ProcessNotification.severity` type is `"info" \| "warning" \| "urgent"` — there is no `"error"` value); exit code 137 is reported as killed by SIGKILL |
| R15a | OOM attribution: when a process exits with 137 *and* carried a memory limit, reconciliation queries the scope's `Result` (via `systemctl --user show <scope> -p Result`); a `Result=oom-kill` records `stop_reason="oom_killed"` on the record (no schema change — reuses the `stop_reason` column) and the notification reports "was killed by the OOM killer (<N>MB limit)." rather than a plain SIGKILL. Unlimited processes are not probed. When systemd is unavailable the check degrades to "not OOM" and the plain SIGKILL message is used |
| R16 | A bootstrap hook creates the processes directory, probes `systemd-run` once, and reconciles records whose pids died while the host was down — without dispatching notifications |
| R17 | Liveness is checked via signal 0 (EPERM counts as alive); PID reuse is accepted — a record kept alive by a reused pid reconciles when that pid eventually exits |
| R18 | If the database write fails after a successful spawn, the spawned process group is SIGKILLed and the error propagates; no record is persisted |

## Behaviors

### Spawning (R0, R1, R3, R4, R5, R18)

Spawning detaches the command into its own process group, wires its output to per-process files, and persists the record before returning to the agent.

**Acceptance Criteria**:
- Given the agent calls `dispatch_detached_process` with a valid `name` and `command`, then a record is persisted with `status="running"` and the response includes the record ID, PID, and stdout/stderr paths
- Given a `command` using shell features (`;`, pipes, `>&2`), then it is interpreted by `sh -c` and works as a user would expect
- Given `cwd` and `env` arguments, then the process runs in that directory with the OS environment plus the provided overrides
- Given a blank or whitespace-only `name` or `command`, then the tool returns an error naming the offending field and nothing is spawned
- Given the spawn itself fails before the child runs, then the error propagates and no record is persisted
- Given the database write fails after the child spawned, then the child's process group is sent SIGKILL and the error propagates

### Memory Limiting (R6, R7)

**Acceptance Criteria**:
- Given `systemd-run` is available and an effective limit of N MB, then the command is spawned as `systemd-run --user --scope --quiet --unit=tachikoma-<id>.scope -p MemoryMax=NM -- sh -c <command>` and the record stores the limit
- Given `systemd-run` is unavailable, then a warning is logged, the command spawns as a plain `sh -c`, and the record stores no limit
- Given no per-call limit and `defaultMemoryLimitMb = 0`, then no wrapping occurs
- Given `memory_limit_mb` below 1, then the tool returns a validation error and no process is spawned
- Given `memory_limit_mb` greater than the host's total system RAM, then the tool returns a validation error naming the system RAM and no process is spawned

### Listing and Inspection (R8, R8a, R17)

**Acceptance Criteria**:
- Given `query_process` with no arguments, then running records are listed with ID, name, PID, command, and start time; records whose pid is dead are reconciled and excluded from the running list
- Given `query_process` with `archived=true`, then exited records are listed with exit time and exit code; an OOM-killed record is annotated with "OOM-killed" on its exit line
- Given no matching records, then a clear "No running/exited processes found." message is returned
- Given `query_process` with a `process_id` whose process has exited but is still marked running, then the record is reconciled and the response shows `exited` with the exit code
- Given `query_process` with a `process_id` for a *running* limited process, then the response includes both the memory limit and the live memory usage (in MB); when no live reading is available the usage line is omitted
- Given `query_process` with a `process_id` for an exited OOM-killed process, then the details include a "Stopped: OOM-killed" line
- Given an unknown `process_id`, then a "not found" error is returned

### Reading Output (R9, R9a)

**Acceptance Criteria**:
- Given `read_process_output` with only a `process_id`, then the tail of the stdout log is returned; with `stream="stderr"`, the stderr log
- Given the log is empty or missing, then "No output yet." is returned
- Given a log larger than pi's tail limits, then the response is truncated to the most recent output and prefixed with `[earlier output truncated]`
- Given `offset` and/or `count`, then the `[offset, offset + count)` line slice of the selected stream is returned (offsets 0-based; the stream selection still applies)
- Given a window that begins at or past the last line, then a message names the requested line range and the log's line count rather than returning empty content
- Given a negative `offset` or a `count` below 1, then a validation error is returned before reading
- Given an unknown `process_id`, then a "not found" error is returned

### Renaming (R9b)

**Acceptance Criteria**:
- Given `rename_process` with a non-blank `name`, then the record's stored `name` is updated and the running process is unaffected
- Given a blank or whitespace-only `name`, then a validation error is returned and the record is unchanged
- Given an unknown `process_id`, then a "not found" error is returned

### Deleting (R9c)

**Acceptance Criteria**:
- Given `delete_process` on an exited (or dead-pid) record, then the record is removed from the tracked list and a confirmation naming the record is returned; the on-disk log files are left untouched
- Given `delete_process` on a record that is still `running` with a live pid, then an error is returned telling the user to terminate it first and the record is unchanged
- Given an unknown `process_id`, then a "not found" error is returned

### Termination (R10, R11)

**Acceptance Criteria**:
- Given `terminate_process` on a running record, then `stop_reason` is set before SIGTERM is sent to the process group, the tool waits up to `grace_seconds`, and the reconciled exit (e.g. code 143) is reported without dispatching a notification
- Given the process ignores SIGTERM, then SIGKILL is sent after the grace period and the process dies
- Given `grace_seconds=0`, then the tool returns "Signal sent" immediately and the watcher later reconciles the exit, suppressed by the stored `stop_reason`
- Given the record's process already exited, then the record is reconciled silently and "already stopped" is returned with the exit code
- Given an unknown signal name, then the tool returns an error before signalling
- Given signalling fails with EPERM, then `stop_reason` is cleared and a permission error is returned without altering the record's status

### Exit Detection and Notification (R12, R13, R14, R15, R15a)

**Acceptance Criteria**:
- Given a process exits with code 0 while the host is alive, then the next watcher tick reconciles the record (`exited`, `exitCode=0`, `exitedAt` set) and emits a `"notify"` event with severity `info` and the message "Process '<name>' (id: <id>) exited with code 0."
- Given a non-zero exit code that is not an OOM kill, then the emitted event has severity `warning`; an OOM kill (see R15a) emits severity `urgent`
- Given exit code 137 on a process with no memory limit, then the message reports the process "was killed by signal (SIGKILL)."
- Given exit code 137 on a limited process whose scope `Result` is `oom-kill`, then the record gains `stop_reason="oom_killed"` (recorded atomically with the exit transition) and the message reports the process "was killed by the OOM killer (<N>MB limit)."
- Given exit code 137 on a limited process whose scope was not OOM-killed (or systemd is unavailable), then the plain SIGKILL message is used and no OOM stop reason is recorded
- Given a record was already reconciled by another path (lazy reconciliation, terminate, or a racing watcher tick), then the conditional UPDATE makes the loser a no-op and no duplicate notification is emitted
- Given checking one record throws, then the error is logged and the sweep continues with the remaining records

### Startup Recovery (R2, R16)

**Acceptance Criteria**:
- Given Tachikoma restarts and a `running` record's pid is dead, then the bootstrap reconciles it to `exited`, reading the child-written exit code from the sidecar (a normal exit writes it before dying); the exit code is `null` only when no sidecar exists — a signal kill while the host was also down, or a record predating the wrapper. No notification is emitted
- Given a `running` record whose pid is still alive, then the record stays `running` and tools keep operating on it
