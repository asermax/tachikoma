# ADR-008: Logging Library

**Status**: Accepted
**Date**: 2026-06-11

## Context

Tachikoma runs as a long-lived background service; diagnosing boundary decisions, pipeline runs, and task executions after the fact requires structured logs with consistent component attribution — structured logging with per-subsystem loggers, applied with discipline.

## Decision

Use **pino** for structured logging.

- A root logger created by the core from configuration (level, destination)
- **Per-component child loggers**: every core component and every extension gets `logger.child({ component })` (extensions receive theirs through the `AppContext`), so each line is attributable
- JSON lines to stdout/file in service mode; `pino-pretty` as a dev-only transport for human-readable output
- Contextual bindings (session ID, task ID) attached via child loggers at the call site rather than string interpolation

## Consequences

### Positive

- Fastest mainstream Node logger; negligible overhead even at debug level
- Child loggers make component- and session-scoped filtering trivial (`jq 'select(.component == "boundary")'`)
- JSON output integrates with journald/any log shipper without format adapters

### Negative

- Raw JSON is unreadable without `pino-pretty`; the dev/service output split must be wired in config
- Transports run in worker threads — a little lifecycle complexity on shutdown (flush before exit)

## Alternatives Considered

- **winston**: flexible but slower, heavier, and its transport/format matrix invites inconsistency
- **console + manual JSON**: no levels, no child bindings, no transports — reinvents pino badly
