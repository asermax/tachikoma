# Detached Processes

OS-level commands that outlive turns and restarts. Owned by the detached-processes
extension.

## Tools

| Tool | Role |
|------|------|
| `dispatch_detached_process` | Start a command detached, capturing output to files |
| `query_process` | Check a process's state |
| `read_process_output` | Read captured output — **both stdout and stderr** by default |
| `rename_process` / `delete_process` | Manage the record |
| `terminate_process` | Stop a running process |

Output capture is the reason the stderr default matters: many programs (Python logging,
build tools, servers) write their useful output, errors, and progress to stderr, so a
process can look silent while working or dead while running — read before judging.

## Lifecycle

When `systemd-run` is available and a limit applies (per-process when given, else
`defaultMemoryLimitMb`, default 1024 MB), the process runs under a named systemd scope
enforcing it; with no limit (`0`, or no systemd) it is a plain detached command. A watcher
(`watchIntervalSeconds`, default 15s) notices exits and sends a completion notification.
Processes running under the same Tachikoma instance across a restart are reconciled at
startup, so records survive restarts.

## Configuration

`[extensions.detached-processes]`: `defaultMemoryLimitMb` (default `1024`; `0` = no default
limit — and then no systemd scope), `watchIntervalSeconds` (default `15`).
