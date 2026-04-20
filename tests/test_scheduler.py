"""Tests for the central scheduler and its triggers."""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tachikoma import scheduler as scheduler_mod
from tachikoma.scheduler import (
    CronTrigger,
    IntervalTrigger,
    Job,
    scheduler,
)


class TestIntervalTrigger:
    def test_first_fire_is_immediate(self) -> None:
        trigger = IntervalTrigger(10)
        assert trigger.should_fire(datetime.now(UTC))

    def test_does_not_fire_until_interval_elapsed(self) -> None:
        trigger = IntervalTrigger(10)
        start = datetime.now(UTC)
        trigger.record_fire(start)

        assert not trigger.should_fire(start + timedelta(seconds=5))
        assert trigger.should_fire(start + timedelta(seconds=10))
        assert trigger.should_fire(start + timedelta(seconds=20))

    def test_zero_or_negative_interval_rejected(self) -> None:
        with pytest.raises(ValueError):
            IntervalTrigger(0)

        with pytest.raises(ValueError):
            IntervalTrigger(-1)


class TestCronTrigger:
    def test_rejects_invalid_cron(self) -> None:
        with pytest.raises(ValueError):
            CronTrigger("not a cron", ZoneInfo("UTC"))

    def test_does_not_fire_retroactively_on_startup(self) -> None:
        """Seeding last-fire at construction time prevents catch-up firings."""
        # 3 AM daily: if the scheduler starts at 3:30 AM, it should wait
        # until tomorrow 3 AM, not fire immediately.
        trigger = CronTrigger("0 3 * * *", ZoneInfo("UTC"))

        now = datetime.now(UTC)
        # Immediately after construction, should NOT fire
        assert not trigger.should_fire(now)

    def test_fires_after_cron_boundary_crossed(self) -> None:
        """Advancing past the next cron boundary triggers a fire."""
        trigger = CronTrigger("* * * * *", ZoneInfo("UTC"))
        # Cron `* * * * *` matches every minute — next fire is the next minute.
        future = datetime.now(UTC) + timedelta(minutes=2)
        assert trigger.should_fire(future)


class TestScheduler:
    @pytest.mark.asyncio
    async def test_dispatches_job_concurrently_without_blocking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two jobs due on the same tick run concurrently — neither blocks the other."""
        # Speed up scheduler tick
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        a_started = asyncio.Event()
        b_started = asyncio.Event()

        async def job_a() -> None:
            a_started.set()
            await b_started.wait()

        async def job_b() -> None:
            b_started.set()
            await a_started.wait()

        jobs = [
            Job("a", IntervalTrigger(0.01), job_a),
            Job("b", IntervalTrigger(0.01), job_b),
        ]

        task = asyncio.create_task(scheduler(jobs))

        try:
            await asyncio.wait_for(
                asyncio.gather(a_started.wait(), b_started.wait()),
                timeout=1,
            )
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_single_flight_prevents_overlapping_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A job whose run lasts longer than its interval does not double-spawn."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        started_count = 0

        async def slow_job() -> None:
            nonlocal started_count
            started_count += 1
            await asyncio.sleep(0.3)

        jobs = [Job("slow", IntervalTrigger(0.01), slow_job)]

        task = asyncio.create_task(scheduler(jobs))
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Even though ticks are faster than job execution, only one job started
        assert started_count == 1

    @pytest.mark.asyncio
    async def test_exception_in_one_job_does_not_affect_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        healthy_runs = 0

        async def failing_job() -> None:
            raise RuntimeError("boom")

        async def healthy_job() -> None:
            nonlocal healthy_runs
            healthy_runs += 1

        jobs = [
            Job("failing", IntervalTrigger(0.01), failing_job),
            Job("healthy", IntervalTrigger(0.01), healthy_job),
        ]

        task = asyncio.create_task(scheduler(jobs))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert healthy_runs > 0

    @pytest.mark.asyncio
    async def test_cancellation_cancels_in_flight_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        cancelled_seen = asyncio.Event()

        async def long_job() -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled_seen.set()
                raise

        jobs = [Job("long", IntervalTrigger(0.01), long_job)]

        task = asyncio.create_task(scheduler(jobs))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert cancelled_seen.is_set()
