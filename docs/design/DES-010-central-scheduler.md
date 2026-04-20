# DES-010: Central Scheduler for Time-Based Recurring Work

**Scope**: Python / Architecture
**Status**: Established
**Date**: 2026-04-19
**First Used**: DLT-104

## Problem

The application needs to run many unrelated pieces of work on a recurring schedule — evaluating task definitions, enqueueing session tasks, dispatching background executors, sweeping expired waiters, cleaning up fired one-shot definitions, and so on. Written ad-hoc, each of these becomes its own `while True: ... await asyncio.sleep(n)` loop owned by a subsystem, with its own try/except, its own cancellation handling, and its own cadence constant.

That shape has three costs:
- **Duplication**: every recurring loop re-implements the same scaffolding.
- **Coverage gaps**: adding a new cadence (e.g. once per day) means a new loop, so in practice people fold work into whatever loop already exists at a similar cadence — coupling unrelated concerns.
- **Shutdown fragility**: each loop owns its own cancellation path, and subsystems that also own in-flight resources (executor tasks, DB sessions) have to handle shutdown inline.

## Solution

All time-based recurring work runs through a single central `scheduler` loop defined in `src/tachikoma/scheduler.py`. The scheduler's only responsibility is **dispatch**: on each tick, evaluate a list of `Job`s, concurrently spawn those whose `Trigger` reports ready, and move on. Jobs themselves are zero-arg async callables — dependencies captured by closure at Job construction. The scheduler owns: concurrency isolation between jobs (`asyncio.create_task` per firing), per-job single-flight (no overlapping runs of the same job), error containment (exceptions inside a job are logged, never propagate), and cascaded cancellation (cancelling the scheduler cancels all in-flight job tasks and awaits them).

Two trigger kinds are provided:
- `IntervalTrigger(seconds)` — fires every N seconds; first fire is immediate.
- `CronTrigger(expression, tz)` — fires when a cron expression's next occurrence has passed. Seeds last-fire at construction so startup never triggers retroactive firings for past-today matches.

## When to Use

Put work through the scheduler when:
- It runs on a schedule (cron or fixed interval), **and**
- Its cadence isn't reactive to a specific event (FS change, IPC, queue push).

Do NOT move these to the scheduler:
- **Event-driven loops** (`Buffer._loop`, `watch_skills`, `event_driven_watcher`) — they react to events, not clocks.
- **Specialized polling** with sub-second reactivity requirements (`polling_watcher`'s 5s poll) — diluting through a generic scheduler would hurt responsiveness.
- **Loops tightly coupled to a single object's state** (e.g. `Coordinator._idle_post_processing_loop`, which reads `is_busy`, `_last_message_time`, and the session registry on each iteration) — the decoupling cost outweighs the benefit.

## How to Use

### Adding a Job

1. Write an idempotent tick function: `async def my_job_tick(...) -> None`. One pass, no loop, no sleep, no top-level try/except (the scheduler handles it).
2. If the work needs cross-tick state (e.g. a semaphore, a dict of in-flight children), wrap the tick in a class with a `tick()` method and a `shutdown()` method that drains/cancels any spawned child tasks.
3. Register a `Job` in `__main__.py` where the scheduler is wired:

   ```python
   Job(
       name="my_job",
       trigger=IntervalTrigger(60),  # or CronTrigger("0 3 * * *", tz)
       run=lambda: my_job_tick(repo, settings),
   )
   ```

4. If the job owns long-lived child tasks, call its `shutdown()` in `__main__.py`'s `finally` block, *after* the scheduler task has been cancelled and awaited.

### Interval vs Cron

- Use `IntervalTrigger` for "every N seconds" work that doesn't need clock alignment (task evaluation, dispatch loops, sweeps).
- Use `CronTrigger` for clock-aligned work ("daily at 3 AM", "every hour on the hour") — typically maintenance or batch work.

## Example

```python
# src/tachikoma/tasks/scheduler.py — tick function
async def instance_generator_tick(
    repository: TaskRepository,
    settings: TaskSettings,
) -> None:
    """One pass. No loop, no sleep."""
    definitions = await repository.list_enabled_definitions()
    for definition in definitions:
        # ... evaluate schedule, create instance ...
```

```python
# src/tachikoma/tasks/executor.py — stateful runner
class BackgroundTaskRunner:
    def __init__(self, ...):
        self._semaphore = asyncio.Semaphore(...)
        self._running_tasks: dict[str, asyncio.Task] = {}

    async def tick(self) -> None:
        # spawn executor tasks, prune completed

    async def shutdown(self) -> None:
        # called from __main__ finally, AFTER scheduler cancel
        for task in self._running_tasks.values():
            task.cancel()
        await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
```

```python
# src/tachikoma/__main__.py — wiring
background_runner = BackgroundTaskRunner(...)

jobs = [
    Job("instance_generator",   IntervalTrigger(60), lambda: instance_generator_tick(repo, settings)),
    Job("background_runner",    IntervalTrigger(30), run=background_runner.tick),
    Job("expired_waiter_sweep", IntervalTrigger(120), lambda: expired_waiter_sweep(repo, settings, bus)),
    Job("one_shot_cleanup",     CronTrigger("0 3 * * *", tz), lambda: one_shot_cleanup_tick(repo, settings)),
]
scheduler_tasks.append(asyncio.create_task(scheduler(jobs), name="scheduler"))

# ... run application ...

# shutdown
for task in scheduler_tasks: task.cancel()
await asyncio.gather(*scheduler_tasks, return_exceptions=True)
await background_runner.shutdown()  # drain child tasks AFTER scheduler is down
```

## Benefits

- **Single dispatch path**: one loop, one shutdown, one logging component.
- **Cadences decouple from ownership**: a daily cleanup doesn't piggyback on a 60s loop just because that loop exists.
- **Jobs are unit-testable as ticks**: tests call the tick function directly; no `create_task/sleep/cancel` dance, no flakiness from timing.
- **Per-job error isolation**: a failing job logs and continues — it never stalls siblings.
- **Explicit single-flight**: long-running ticks don't double-spawn when the interval elapses mid-run.

## Trade-offs

| Aspect | Trade-off |
|--------|-----------|
| Cadence resolution | Fixed 1s scheduler tick — fine for every current job (min interval 30s); would need tuning for sub-second work |
| State-owning jobs | Need a class + `shutdown()` rather than a plain function — slightly heavier than the raw loop, but shutdown is explicit and composable |
| Startup semantics | `CronTrigger` seeds last-fire at construction to avoid retroactive firings; document this so "why didn't it fire at boot?" is answered |
| Coupled loops | Loops tightly bound to an object's state are still left outside the scheduler (e.g., `Coordinator._idle_post_processing_loop`) — migrating them would leak that state into generic scheduler wiring |

## Related Patterns

- **Bootstrap hooks (DES-003)**: the scheduler and its jobs are wired in `__main__.py` after bootstrap completes; jobs close over objects constructed during bootstrap.
- **Logging conventions (DES-002)**: scheduler logs under `component="scheduler"`; each job's tick logs under its own component.

## See Also

- `src/tachikoma/scheduler.py` — Scheduler, Job, IntervalTrigger, CronTrigger
- `src/tachikoma/tasks/scheduler.py` — tick entry points for instance generation, session dispatch, one-shot cleanup
- `src/tachikoma/tasks/executor.py` — `BackgroundTaskRunner` (stateful-tick class pattern) and `expired_waiter_sweep`
- `src/tachikoma/__main__.py` — job registration and shutdown order
