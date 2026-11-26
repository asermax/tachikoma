# ADR-006: Scheduler

**Status**: Accepted
**Date**: 2026-06-11

## Context

The task system needs timezone-aware cron schedules and one-shot datetime targets, evaluated in-process (pi deliberately provides no scheduling — it is a host concern). Other extensions also need periodic ticks (maintenance, idle checks). The scheduler is core infrastructure exposed through the `AppContext`.

## Decision

Use **croner** for all in-process scheduling.

- `Cron` instances for recurring schedules with native timezone support, overrun protection, and pause/resume
- One-shot tasks scheduled by passing a target `Date`
- Next-run queries (`nextRun()`) power restart catch-up: persisted last-run state in the database is compared against the schedule to detect and run missed executions

Persistence is explicitly not the scheduler's job — task definitions and execution history live in the database (ADR-004); croner only owns the in-memory timing.

Overrun protection is symmetric across both schedule kinds, because both are croner jobs. Cron jobs pass `protect: true`. Interval (`every`) jobs use croner's `interval` option with a `protect` callback, so a tick that arrives while the previous run is still active is skipped and logged instead of overlapping itself. Manual `trigger()` always runs — it deliberately bypasses overrun protection, since an explicit invocation should never be silently dropped.

## Consequences

### Positive

- Zero dependencies, pure TypeScript, no native code — consistent with the rest of the stack
- Timezone-aware cron expressions support user-local schedules natively
- Overrun protection and pause/resume cover the idle-gating and user-activity interactions the task system needs
- Predictable testing: schedules are objects that can be queried for next runs, and vitest fake timers drive them deterministically

### Negative

- In-process only: schedules die with the process — catch-up logic for missed runs is ours, backed by the database
- Long-horizon one-shot timers rely on the process staying up; restart catch-up is the safety net
- Cron expressions are parsed by croner, strictly: an expression it rejects throws at construction. Tachikoma turns that into a `ScheduleError` when a schedule is written and into a logged error for that one definition on the tick, so a stored expression croner rejects is skipped on every tick until the definition is edited
- croner follows Vixie cron across DST transitions: a wall-clock time that does not exist never matches, so a fixed-time job inside a spring-forward gap fires at the first valid time after the gap and an interval pattern skips the missing times. An expression that can never match has no next run (`nextRun()` is `null`, not an error), and an `every` interval is elapsed time, so a transition neither shortens nor stretches it. `tests/tasks/schedule.test.ts` and `tests/scheduler.test.ts` pin the behavior

## Alternatives Considered

- **node-cron**: less precise timezone handling and no overrun protection
- **BullMQ / persistent job queues**: brings Redis or heavyweight infrastructure to a single-user, single-process service
- **Hand-rolled setTimeout loop**: what the cron libraries exist to get right (DST, drift, validation)
