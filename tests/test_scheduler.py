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


class TestPriorityDispatch:
    """Tests for the priority-aware bounded dispatch mechanism."""

    def test_default_priority_is_high(self) -> None:
        job = Job("x", IntervalTrigger(10), noop)
        assert job.priority == "high"

    @pytest.mark.asyncio
    async def test_low_priority_jobs_share_semaphore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC2: With max_concurrent_low=1, only one low-priority job runs at a time."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        running = 0
        max_running = 0
        blocker = asyncio.Event()

        async def tracked_job() -> None:
            nonlocal running, max_running
            running += 1
            max_running = max(max_running, running)
            await blocker.wait()
            running -= 1

        jobs = [
            Job("low_a", IntervalTrigger(0.01), tracked_job, priority="low"),
            Job("low_b", IntervalTrigger(0.01), tracked_job, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        await asyncio.sleep(0.1)
        blocker.set()
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert max_running == 1

    @pytest.mark.asyncio
    async def test_high_priority_bypasses_semaphore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AC3: High-priority job dispatches while a low-priority job holds the semaphore."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        high_entered = asyncio.Event()
        low_blocker = asyncio.Event()

        async def low_job() -> None:
            await low_blocker.wait()

        async def high_job() -> None:
            high_entered.set()

        jobs = [
            Job("low", IntervalTrigger(0.01), low_job, priority="low"),
            Job("high", IntervalTrigger(0.01), high_job, priority="high"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        assert await asyncio.wait_for(high_entered.wait(), timeout=1)
        low_blocker.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_low_priority_single_flight_during_semaphore_wait(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC7: A low-priority job awaiting the semaphore is still in-flight."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        second_started = asyncio.Event()
        blocker = asyncio.Event()

        async def blocking_job() -> None:
            await blocker.wait()

        async def waiting_job() -> None:
            second_started.set()

        jobs = [
            Job("blocker", IntervalTrigger(0.01), blocking_job, priority="low"),
            Job("waiter", IntervalTrigger(0.01), waiting_job, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        await asyncio.sleep(0.2)

        # waiting_job should NOT have started — it's queued behind blocker
        assert not second_started.is_set()

        blocker.set()
        await asyncio.sleep(0.1)
        # Now waiting_job should have run
        assert second_started.is_set()

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_low_priority_exception_releases_semaphore(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC8: Exception in a low-priority job releases the semaphore."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        second_ran = asyncio.Event()

        async def failing_job() -> None:
            raise RuntimeError("boom")

        async def succeeding_job() -> None:
            second_ran.set()

        jobs = [
            Job("failer", IntervalTrigger(0.01), failing_job, priority="low"),
            Job("succeeder", IntervalTrigger(0.01), succeeding_job, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        assert await asyncio.wait_for(second_ran.wait(), timeout=2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_scheduler_works_without_low_priority_jobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Smoke: only high-priority jobs works identically to pre-priority behavior."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        ran = asyncio.Event()

        async def simple_job() -> None:
            ran.set()

        jobs = [Job("simple", IntervalTrigger(0.01), simple_job)]
        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        assert await asyncio.wait_for(ran.wait(), timeout=1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_low_priority_dispatch_order_preserved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC5/R8: Low-priority jobs dispatch in registration order under FIFO semaphore."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        acquired: list[str] = []
        release_a = asyncio.Event()
        release_b = asyncio.Event()
        release_c = asyncio.Event()

        async def job_a() -> None:
            acquired.append("a")
            await release_a.wait()

        async def job_b() -> None:
            acquired.append("b")
            await release_b.wait()

        async def job_c() -> None:
            acquired.append("c")
            await release_c.wait()

        jobs = [
            Job("a", IntervalTrigger(0.01), job_a, priority="low"),
            Job("b", IntervalTrigger(0.01), job_b, priority="low"),
            Job("c", IntervalTrigger(0.01), job_c, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        await asyncio.sleep(0.1)

        assert acquired == ["a"]
        release_a.set()
        await asyncio.sleep(0.1)

        assert acquired == ["a", "b"]
        release_b.set()
        await asyncio.sleep(0.1)

        assert acquired == ["a", "b", "c"]
        release_c.set()
        await asyncio.sleep(0.05)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_cascaded_cancellation_with_pending_low_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC11: Cancelling scheduler cancels running low job and cleans up."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        running_cancelled = asyncio.Event()
        blocker = asyncio.Event()

        async def running_job() -> None:
            try:
                await blocker.wait()
            except asyncio.CancelledError:
                running_cancelled.set()
                raise

        async def waiting_job() -> None:
            await asyncio.sleep(10)

        jobs = [
            Job("running", IntervalTrigger(0.01), running_job, priority="low"),
            Job("waiting", IntervalTrigger(0.01), waiting_job, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=1))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Running job got cancelled
        assert running_cancelled.is_set()
        # After cleanup, a fresh scheduler with a low-priority job can
        # immediately acquire a fresh semaphore (proves no leak).
        fresh_ran = asyncio.Event()

        async def fresh_job() -> None:
            fresh_ran.set()

        fresh_jobs = [Job("fresh", IntervalTrigger(0.01), fresh_job, priority="low")]
        fresh_task = asyncio.create_task(scheduler(fresh_jobs, max_concurrent_low=1))
        assert await asyncio.wait_for(fresh_ran.wait(), timeout=1)
        fresh_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fresh_task

    @pytest.mark.asyncio
    async def test_unbounded_when_limit_exceeds_low_job_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC12: Low-priority jobs run concurrently when limit >= count of low jobs."""
        monkeypatch.setattr(scheduler_mod, "TICK_SECONDS", 0.01)

        both_running = asyncio.Event()
        blocker = asyncio.Event()
        running = 0

        async def job_a() -> None:
            nonlocal running
            running += 1
            if running == 2:
                both_running.set()
            await blocker.wait()
            running -= 1

        async def job_b() -> None:
            nonlocal running
            running += 1
            if running == 2:
                both_running.set()
            await blocker.wait()
            running -= 1

        jobs = [
            Job("a", IntervalTrigger(0.01), job_a, priority="low"),
            Job("b", IntervalTrigger(0.01), job_b, priority="low"),
        ]

        task = asyncio.create_task(scheduler(jobs, max_concurrent_low=5))
        assert await asyncio.wait_for(both_running.wait(), timeout=1)
        blocker.set()
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def noop() -> None:
    pass
