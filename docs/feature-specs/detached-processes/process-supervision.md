# Process Supervision

<!-- This spec describes the current system capability. Updated through delta reconciliation. -->

## Overview

MCP tools let the agent spawn, inspect, read logs from, and terminate detached shell commands. Each spawned process runs in its own session (decoupled from Tachikoma's process group), with combined stdout/stderr redirected to a per-process log file, and its identity (PID + OS-reported start time) recorded in the shared database so that liveness can be checked reliably across restarts. Process records persist across Tachikoma restarts and crashes, and a background watcher proactively detects exits and dispatches a user-facing notification through the priority buffer.

## User Stories

- As a user, I want Tachikoma to spawn OS-level shell commands that outlive the current conversation (and Tachikoma itself) so that the agent can start long-running workers on the host
- As a user, I want to ask Tachikoma to check on previously-started workers, tail their logs, or stop them without having to SSH in
- As a user, I want to be notified when a detached worker exits abnormally so that I can react promptly

## Requirements

| ID | Requirement |
|----|-------------|
| R0 | Provide MCP tools so the agent can spawn, inspect, read logs from, and terminate detached shell commands |
| R1 | Hard detach — spawned processes run in a new session/process group with stdio redirected to a log file, so they survive Tachikoma's exit, restart, or crash |
| R2 | Persist process records in the shared SQLite database (id, name, command, cwd, pid, OS-reported start time, log_path, status, timestamps, exit code when known) |
| R3 | `start_process` tool: spawn a command detached; accept `name` (required), `command` (required, shell string), optional `cwd`, optional `env` overrides; return the record ID and PID |
| R4 | `list_processes` / `get_process` tools: enumerate recorded processes with liveness status; support an `archived` filter on `list_processes` to show exited records |
| R5 | `read_process_output` tool: read from the captured log, tail-by-default (last N lines) with optional line-offset paging so the agent can page through older output |
| R6 | `stop_process` tool: terminate a process; accept an optional signal (default SIGTERM) and an optional timeout, escalating to SIGKILL if still alive after the timeout |
| R7 | `rename_process` tool: update the stored display name of a recorded process without altering its identity |
| R8 | Bootstrap crash-recovery step: on startup, reconcile records marked `running` against actual PID liveness and mark dead ones as `exited` without producing user-facing notifications |
| R9 | Default environment for spawned processes is inherited from Tachikoma's own OS environment; agent-specific env (`settings.agent.env`) is NOT injected; `env` argument, when provided, is merged on top of the OS environment |
| R10 | Capture combined stdout+stderr to `{workspace}/.tachikoma/detached-processes/{id}.log`, appending for the lifetime of the process; log file remains on disk after the process exits |
| R11 | Default `cwd` for spawned processes is Tachikoma's own current working directory; overridable per invocation |
| R12 | PID-reuse protection: alongside the PID, record the process's start time (from the OS) and treat the stored process as "exited" if the current process at that PID has a different start time |
| R13 | Capture the exit code of a terminated process when it can be determined without blocking Tachikoma |
| R14 | Base system prompt preamble mentions the detached-process tools so the agent is aware of the capability regardless of whether any processes are currently running |
| R15 | Proactive exit detection — a subsystem-owned watcher observes `running` records in the background and, when a process exits, updates the record (status, `exited_at`, exit code when known) and dispatches a `Notification` event on the shared event bus so the priority buffer delivers it through the active channel at a natural idle window; the watcher assigns Urgent priority on abnormal exit (non-zero or unknown exit code) and Normal priority on clean exit (exit code 0); exit notifications are suppressed for agent-initiated stops (per R17). The watcher is the sole producer of exit notifications for this subsystem — `stop_process` does not dispatch a notification of its own |
| R16 | Process names are non-unique display labels (no collision handling on `start_process` or `rename_process`) |
| R17 | Agent-initiated stop tracking — a nullable `stop_reason` field on process records marks whether a stop was agent-initiated; when `stop_process` is invoked, `stop_reason` is set to `"agent_stopped"` before the signal is sent so that exit notifications are suppressed for intentionally stopped processes; the field is cleared if signal delivery fails with a permission error; this ensures only unexpected exits produce user-facing notifications |

## Behaviors

### Spawning (R0, R1, R3, R9, R10, R11)

Spawning detaches the command from Tachikoma's process group, redirects stdio to a per-process log file, and persists the record before returning to the agent.

**Acceptance Criteria**:
- Given the agent calls `start_process` with a valid `name`, `command`, `cwd`, and optional `env`, then a new record is persisted with `status=running`, PID, OS-reported start time, and log path populated, and the tool response returns the record ID, PID, and log path
- Given `start_process` is called, then the spawned process runs in a new session (disowned from Tachikoma's process group) with stdin set to `/dev/null` and stdout+stderr redirected to the log file
- Given Tachikoma is terminated (normal exit, crash, or `kill -9`), then previously-started detached processes continue running (reparented to init) and their log files continue to receive output
- Given `start_process` is called with no `cwd`, then the process is spawned in Tachikoma's own current working directory
- Given `start_process` is called with no `env` argument, then the spawned process's environment is Tachikoma's OS environment and does not include values from `settings.agent.env`
- Given `start_process` is called with an `env` mapping, then the spawned process's environment is Tachikoma's OS environment with the provided keys overridden/added on top (and still excluding `settings.agent.env`)
- Given `start_process` is called with a `cwd` that does not exist or is not a directory, then the tool returns a clear error and no record is persisted
- Given `start_process` is called with an empty or whitespace-only `name` or `command`, then the tool returns a clear error identifying the offending field
- Given `start_process` is called and the spawn itself fails (e.g. fork/exec error before the child can run), then the tool returns a clear error and no record is persisted
- Given `start_process` is called with a `command` containing shell features (pipes, redirections, `&&`), then the command is interpreted by a shell so those features work as a user would expect
- Given `start_process` is called and the database write fails after the child process has already been spawned, then the spawned child is terminated (best-effort SIGKILL) so that no orphan process without a corresponding record is left running
- Given the log directory cannot be created or is not writable at spawn time, then the tool returns a clear error and no process is spawned or record persisted
- Given `start_process` is called with a `name` equal to the name of an existing record, then the new record is created normally — names are non-unique display labels (R16)

### Listing and Inspection (R0, R4, R12, R13)

List and get tools enumerate records with accurate liveness, guarding against PID reuse by comparing the recorded OS start time.

**Acceptance Criteria**:
- Given the agent calls `list_processes` with no arguments, then it receives every record whose current status is `running`, each entry showing record ID, name, command, PID, cwd, started_at (in the configured timezone), and a running/exited status string
- Given the agent calls `list_processes` with `archived=true`, then it receives every record whose current status is `exited`, each entry showing record ID, name, command, started_at, exited_at (if known), and exit code (if known)
- Given the agent calls `list_processes` and no matching records exist, then a clear "no processes found" message is returned
- Given the agent calls `get_process` with a valid record ID, then it receives full details including command, cwd, PID, stored start time, log path, status, timestamps, and exit code (if known)
- Given the agent calls `get_process` with an unknown record ID, then a clear "not found" error is returned
- Given a record is marked `running` but the OS reports no process at that PID, then `list_processes`/`get_process` reports the record as `exited`, the record is updated accordingly, and `exited_at` is populated with the detection time
- Given a record is marked `running` and the OS reports a process at that PID but its start time differs from the stored start time (PID reuse), then the record is treated as `exited` (not falsely reported as still alive)
- Given a running process exits of its own accord and the proactive watcher has not yet observed the exit, when the agent next calls a tool that operates on the record, then the record is lazily reconciled to `exited` as a fallback

### Reading Logs (R0, R5, R10)

Log reads tail by default and page by explicit offset. Large log files must not be loaded fully into memory to serve a tail read.

**Acceptance Criteria**:
- Given the agent calls `read_process_output` with a valid record ID and no explicit range, then it receives the last 100 lines of the combined stdout+stderr log
- Given the agent calls `read_process_output` with a line offset and line count, then it receives that window of lines from the log, in order
- Given the log file has not been created yet or is empty, then the agent receives an explicit "no output yet" message rather than an empty response
- Given the log file has been deleted or is unreadable, then the tool returns a clear error
- Given a process has exited, then `read_process_output` still returns the log contents unchanged (log is retained on disk)
- Given a log file is arbitrarily large (many gigabytes), then tail-by-default reads remain fast — the implementation must not load the entire file into memory to serve a tail read

### Termination (R0, R6, R13, R17)

Termination signals the whole process group (not just the wrapper shell) and escalates to SIGKILL after a timeout. Termination never emits a user-facing notification — the watcher is the sole producer (R15). The `stop_reason` field is set before signalling so that the watcher suppresses the exit notification (R17).

**Acceptance Criteria**:
- Given the agent calls `stop_process` on a running record with no arguments, then SIGTERM is sent to the process group and the tool waits up to the default timeout (10 seconds) before escalating to SIGKILL if the process is still alive
- Given the agent calls `stop_process` with an explicit `signal` (e.g. `SIGINT`, `SIGHUP`, `SIGKILL`), then that signal is sent instead of SIGTERM
- Given the agent calls `stop_process` with `timeout=0`, then the signal is sent and the tool returns immediately without polling for exit or escalating; the persisted `stop_reason` flag ensures the watcher later suppresses the exit notification
- Given SIGTERM is sent but the process is still alive after the timeout, then SIGKILL is sent and the tool waits briefly to confirm exit
- Given `stop_process` is called on a record whose process has already exited (detected via liveness check before signalling), then the record is reconciled and the tool returns a clear "already stopped" message
- Given `stop_process` is called with an unknown record ID, then a clear "not found" error is returned
- Given `stop_process` successfully terminates the process, then the record's status, `exited_at`, and exit code (when determinable) are updated
- Given the OS rejects the signal with a permission error, then the tool returns a clear error describing the condition without altering the record's status
- Given `stop_process` reconciles a record from `running` to `exited`, then no `Notification` event is dispatched by `stop_process` — user-facing notification of exit is the watcher's sole responsibility (R15)
- Given `stop_process` successfully sends a signal to the process, then the record's `stop_reason` is set to `"agent_stopped"` before the signal is delivered
- Given `stop_process` fails to deliver the signal due to a permission error, then the `stop_reason` field is cleared (set to None) so that a future natural exit is not incorrectly suppressed

### Proactive Exit Detection and Notification (R15)

A subsystem-owned watcher detects exits in the background and dispatches a `Notification` through the priority buffer. The watcher combines an event-driven mechanism (for fast common-case detection) with a periodic liveness check (to catch cases where no exit-code signal was written).

**Acceptance Criteria**:
- Given one or more records are marked `running`, then a subsystem-owned watcher runs in the background and detects exits via filesystem events on an exit-code sidecar file (primary path) combined with periodic OS-level liveness checks as a fallback, without the agent having to trigger it
- Given the watcher observes that a running process has exited, then the record is updated to `exited` with `exited_at` set and the exit code populated when determinable
- Given the watcher transitions a record from `running` to `exited`, then a `Notification` event is dispatched on the shared event bus (via `dispatch_notification`) describing the process (name, record ID, exit code when known), so the priority buffer enqueues it for idle-gated delivery to the active channel
- Given the watcher detects a clean exit (exit code 0), then the dispatched notification's priority is Normal
- Given the watcher detects an abnormal exit (non-zero exit code, or exit code not determinable), then the dispatched notification's priority is Urgent
- Given the watcher dispatches a notification and Tachikoma is shutting down before the priority buffer delivers it, then delivery follows the priority buffer's shutdown-digest behavior (no dedicated handling in this subsystem)
- Given a record has been reconciled to `exited` by any path (watcher, lazy reconciliation, or `stop_process`), then it is no longer a candidate for the watcher — no duplicate notification is sent for the same record
- Given there are no records marked `running`, then the watcher idles with no work (no notifications, no database writes)
- Given the watcher's liveness check, record update, or notification dispatch raises an error for a particular record, then the error is logged and the watcher continues processing other records and subsequent cycles without terminating
- Given the watcher's cadence, then it is short enough that the worst-case gap between an abnormal exit and the dispatched notification remains small relative to the Urgent idle-window (30s)
- Given Tachikoma is shutting down, then the watcher stops cleanly along with the other scheduler tasks
- Given the watcher transitions a record with `stop_reason='agent_stopped'` from `running` to `exited`, then no `Notification` event is dispatched — exit notifications are suppressed for agent-initiated stops (R17)
- Given the watcher transitions a record with no `stop_reason` (or `stop_reason` is None) from `running` to `exited`, then a `Notification` event is dispatched as normal — only agent-initiated stops are suppressed

### Renaming (R0, R7)

**Acceptance Criteria**:
- Given the agent calls `rename_process` with a valid record ID and a non-empty name, then the stored name is updated and PID/log/status are untouched
- Given `rename_process` is called with an empty or whitespace-only name, then the tool returns a clear error
- Given `rename_process` is called with an unknown record ID, then a clear "not found" error is returned

### Persistence and Recovery (R2, R8)

**Acceptance Criteria**:
- Given the system starts for the first time, then the bootstrap hook creates the detached-process table in the shared database
- Given Tachikoma restarts, then every previously persisted record is still available via `list_processes` / `get_process` with its stored identity intact
- Given Tachikoma restarts, when the bootstrap hook runs, then every record currently marked `running` is checked via PID + start-time, and any whose process is no longer alive is marked `exited` without dispatching a user-facing notification
- Given Tachikoma restarts and a record's PID is still alive with the recorded start time, then the record remains `running` and subsequent tool calls continue to operate on it as normal

### Preamble Awareness (R14)

**Acceptance Criteria**:
- Given the base system prompt preamble is rendered, then it includes a Detached Processes section that names each of the tools (`start_process`, `list_processes`, `get_process`, `read_process_output`, `stop_process`, `rename_process`) with a one-line description of each and guidance on when the agent should use them

## Requires

Dependencies:
- None

Assumes existing:
- Shared `EventBus` wired into the coordinator and channels (ADR-009)
- Notification dispatch helper and `Notification` event (`tachikoma.notifications`)
- Priority buffer subsystem that enqueues `Notification` events for idle-gated delivery to the active channel (see [delivery/priority-buffer](../delivery/priority-buffer.md))
- Shared SQLAlchemy async persistence layer with a workspace-scoped SQLite database (ADR-007)
- Subsystem bootstrap hook pattern (DES-003)
- SDK MCP tool server factory pattern (DES-006)
- Workspace bootstrap (the `{workspace}/.tachikoma/` data directory exists)
