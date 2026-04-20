"""Central scheduler for time-based recurring work.

A single async loop dispatches registered Jobs concurrently based on
their Triggers. The scheduler is stateless beyond tracking which jobs
are currently running (single-flight guard) — each trigger owns its own
last-fire timestamp.

Jobs do not share the scheduler's cancellation semantics: when the
scheduler is cancelled, all in-flight job tasks are cancelled and
awaited. Jobs with resources that live longer than one tick (e.g. the
background task runner's executor tasks) must expose their own shutdown
mechanism and be drained by the owner after the scheduler is cancelled.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger

_log = logger.bind(component="scheduler")

# How often the scheduler evaluates triggers. 1s is enough resolution for
# every current job (shortest interval is 30s) and cheap enough to run
# every second.
TICK_SECONDS = 1.0


class Trigger(Protocol):
    """Decides whether a job should fire at a given moment."""

    def should_fire(self, now_utc: datetime) -> bool: ...

    def record_fire(self, now_utc: datetime) -> None: ...


class IntervalTrigger:
    """Fires every ``seconds`` seconds. First fire happens immediately."""

    def __init__(self, seconds: float) -> None:
        if seconds <= 0:
            raise ValueError("IntervalTrigger seconds must be positive")

        self._seconds = seconds
        self._last_fire_utc: datetime | None = None

    def should_fire(self, now_utc: datetime) -> bool:
        if self._last_fire_utc is None:
            return True

        return (now_utc - self._last_fire_utc).total_seconds() >= self._seconds

    def record_fire(self, now_utc: datetime) -> None:
        self._last_fire_utc = now_utc


class CronTrigger:
    """Fires when a cron expression's next occurrence has passed.

    Anchored at startup rather than the epoch so jobs never fire
    retroactively for matches that predate the scheduler's start.
    """

    def __init__(self, expression: str, tz: ZoneInfo) -> None:
        # Validate the expression early
        try:
            next(CronSim(expression, datetime.now(tz)))
        except (CronSimError, StopIteration) as exc:
            raise ValueError(f"Invalid cron expression: {expression}") from exc

        self._expression = expression
        self._tz = tz
        # Seed last-fire at construction time so the first fire is the
        # next future occurrence — never a retroactive catch-up
        self._last_fire_tz: datetime = datetime.now(tz)

    def should_fire(self, now_utc: datetime) -> bool:
        now_tz = now_utc.astimezone(self._tz)

        try:
            next_fire_tz = next(CronSim(self._expression, self._last_fire_tz))
        except (CronSimError, StopIteration):
            return False

        return next_fire_tz <= now_tz

    def record_fire(self, now_utc: datetime) -> None:
        self._last_fire_tz = now_utc.astimezone(self._tz)


@dataclass(frozen=True)
class Job:
    """A scheduled unit of work.

    ``run`` is a zero-arg async callable. Dependencies are supplied via
    closure when the Job is constructed.
    """

    name: str
    trigger: Trigger
    run: Callable[[], Awaitable[None]]


async def scheduler(jobs: list[Job]) -> None:
    """Dispatch jobs concurrently based on their triggers.

    Each tick: for every job that isn't already running and whose
    trigger reports ready, spawn a task running the job. Finished tasks
    are pruned after each tick. Exceptions inside a job are caught and
    logged — a failing job never brings the scheduler down and never
    affects sibling jobs.

    On cancellation: all in-flight job tasks are cancelled and awaited
    before the scheduler itself re-raises.
    """
    in_flight: dict[str, asyncio.Task[None]] = {}

    _log.info("Scheduler started with {count} jobs", count=len(jobs))

    try:
        while True:
            now_utc = datetime.now(UTC)

            for job in jobs:
                # Skip if a previous run is still executing (single-flight)
                existing = in_flight.get(job.name)
                if existing is not None and not existing.done():
                    continue

                if not job.trigger.should_fire(now_utc):
                    continue

                job.trigger.record_fire(now_utc)
                in_flight[job.name] = asyncio.create_task(
                    _run_guarded(job),
                    name=f"job:{job.name}",
                )

            # Prune finished jobs
            for name in [n for n, t in in_flight.items() if t.done()]:
                in_flight.pop(name, None)

            await asyncio.sleep(TICK_SECONDS)

    except asyncio.CancelledError:
        _log.info("Scheduler cancelled — cancelling {count} in-flight jobs", count=len(in_flight))

        for task in in_flight.values():
            task.cancel()

        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)

        raise


async def _run_guarded(job: Job) -> None:
    """Run a job, logging any exception without propagating."""
    try:
        await job.run()
    except asyncio.CancelledError:
        _log.info("Job {name} cancelled", name=job.name)
        raise
    except Exception as exc:
        _log.exception(
            "Job {name} failed: {err}",
            name=job.name,
            err=str(exc),
        )
