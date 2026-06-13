# ADR-008: Logging Library

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma runs as a long-lived background service; diagnosing boundary decisions, pipeline runs, and task executions after the fact requires structured logs with consistent component attribution — structured logging with per-subsystem loggers, applied with discipline.

## Decision

Use **pino** for structured logging.

- A root logger created by the core from configuration (level, destination)
- **Per-component child loggers**: every core component and every extension gets `logger.child({ component })` (extensions receive theirs through the `AppContext`), so each line is attributable
- stderr is always written; when `logging.toFile` is set the root logger fans out via **`pino.multistream`** to both stderr and a JSON file under `{workspace}/.tachikoma/logs`, so daemon runs persist a durable record without losing pretty interactive output. Both the file sink and pino-pretty are wired as in-process *streams* (not worker-thread transports) so they compose with multistream
- **Automatic rotation via `pino-roll`** (an in-process SonicBoom stream, not a worker-thread transport): files roll by `logging.rotateFrequency` (`hourly`/`daily`, default daily) *while the process runs*, and `limit.removeOtherLogFiles` prunes across restarts to `retentionDays` of files. Chosen over hand-rolling because pino-roll is the pino-team-maintained mechanism and composes with our existing in-process multistream; the dependency is justified by retiring rotation code we would otherwise own
- Contextual bindings (session ID, task ID) attached via child loggers at the call site rather than string interpolation

## Consequences

### Positive

- Fastest mainstream Node logger; negligible overhead even at debug level
- Child loggers make component- and session-scoped filtering trivial (`jq 'select(.component == "boundary")'`)
- JSON output integrates with journald/any log shipper without format adapters

### Negative

- Raw JSON is unreadable without `pino-pretty`; the dev/service output split must be wired in config
- `pino-roll` (plus its `date-fns` dependency) is an added dependency — accepted to avoid maintaining rotation logic ourselves
- Rotation is in-process: a pino-roll/pino-pretty stream is a SonicBoom, so we deliberately avoid worker-thread transports (which can't compose with multistream and add shutdown-flush complexity)
- `pino-roll` rotates only by `hourly`/`daily`/numeric-ms; `weekly` is not supported (the config enum is constrained to match)

## Alternatives Considered

- **Custom in-house rotation** (the prior decision: `rotateLogs`, archive-on-startup + mtime prune): zero dependencies, but rotated only at process start — a long-lived daemon never rolled mid-run — and reimplemented what `pino-roll` already does. Superseded once continuous rotation became a requirement
- **`logrotate` + `SIGHUP`/`dest.reopen()`**: standard, but assumes an external daemon and adds out-of-process coordination; pino-roll keeps rotation self-contained
- **winston**: flexible but slower, heavier, and its transport/format matrix invites inconsistency
- **console + manual JSON**: no levels, no child bindings, no transports — reinvents pino badly
