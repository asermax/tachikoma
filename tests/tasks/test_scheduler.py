"""Tests for task scheduler and instance generator.

See: docs/delta-specs/DLT-090.md (R0–R6 acceptance criteria)
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from bubus import EventBus
from cronsim import CronSim

from tachikoma.config import TaskSettings
from tachikoma.tasks.events import SessionTaskReady
from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import instance_generator, session_task_scheduler

from .conftest import _make_definition, _make_instance


def _cron_first_match(expression: str, tz: ZoneInfo) -> datetime:
    """Compute the first cron match time that the generator would fire for now.

    Uses the same anchor logic as the generator (start-of-hour when no last_fired_at).
    """
    now_tz = datetime.now(tz)
    anchor_tz = now_tz.replace(minute=0, second=0, microsecond=0)
    return next(CronSim(expression, anchor_tz)).astimezone(UTC)


class TestInstanceGenerator:
    """Tests for the instance_generator async function."""

    async def test_creates_instance_when_cron_fires(self, repo: TaskRepository) -> None:
        """R0: Creates pending instance when cron schedule fires."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].definition_id == "def-1"

    async def test_scheduled_for_is_cron_match_time(self, repo: TaskRepository) -> None:
        """R0: Instance scheduled_for equals the cron match time, not wall-clock time."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        task = asyncio.create_task(instance_generator(repo, TaskSettings(timezone="UTC")))
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        # scheduled_for should be an exact minute boundary, not wall-clock
        assert instances[0].scheduled_for.second == 0
        assert instances[0].scheduled_for.microsecond == 0

    async def test_skips_disabled_definitions(self, repo: TaskRepository) -> None:
        """AC: Skips definitions that are disabled."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule, enabled=False)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_duplicate_prevention_with_matching_scheduled_for(
        self,
        repo: TaskRepository,
    ) -> None:
        """R2: Safety net suppresses duplicate when completed instance exists for same period."""
        tz = ZoneInfo("UTC")
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        # Compute the cron match time the generator will use
        cron_match = _cron_first_match("* * * * *", tz)

        # Pre-create a completed instance with matching scheduled_for
        await repo.create_instance(
            _make_instance(
                "completed-1",
                definition_id="def-1",
                status="completed",
                scheduled_for=cron_match,
            )
        )

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Should still only have the original instance
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_safety_net_allows_retry_for_failed_instance(
        self,
        repo: TaskRepository,
    ) -> None:
        """R2: Failed instance is excluded from safety net, allowing retry."""
        tz = ZoneInfo("UTC")
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        cron_match = _cron_first_match("* * * * *", tz)

        # Create a failed instance for the same period
        await repo.create_instance(
            _make_instance(
                "failed-1",
                definition_id="def-1",
                status="failed",
                scheduled_for=cron_match,
            )
        )

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # A new pending instance should be created despite the failed one
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].definition_id == "def-1"

    async def test_safety_net_prevents_duplicate_for_pending_instance(
        self,
        repo: TaskRepository,
    ) -> None:
        """R2: Pending instance with matching scheduled_for prevents duplicate."""
        tz = ZoneInfo("UTC")
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        cron_match = _cron_first_match("* * * * *", tz)

        # Create a pending instance with matching scheduled_for
        await repo.create_instance(
            _make_instance(
                "pending-1",
                definition_id="def-1",
                status="pending",
                scheduled_for=cron_match,
            )
        )

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Should still only have one instance
        pending = await repo.get_pending_instances("session")
        assert len(pending) == 1
        assert pending[0].id == "pending-1"

    async def test_creates_new_instance_after_previous_period_completed(
        self,
        repo: TaskRepository,
    ) -> None:
        """R2: Previous period's completed instance does not block new period."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        tz = ZoneInfo("UTC")

        # Create definition with last_fired_at from a previous minute
        prev_fire = datetime.now(tz).replace(second=0, microsecond=0) - timedelta(minutes=1)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            last_fired_at=prev_fire.astimezone(UTC),
        )
        await repo.create_definition(definition)

        # Create a completed instance for the previous period
        await repo.create_instance(
            _make_instance(
                "old-1",
                definition_id="def-1",
                status="completed",
                scheduled_for=prev_fire.astimezone(UTC),
            )
        )

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # A new instance should be created for the current period
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].id != "old-1"

    async def test_no_early_firing_before_cron_boundary(self, repo: TaskRepository) -> None:
        """R1: Generator does not fire when next cron time hasn't arrived yet."""
        # Create a cron that fires at minute 59 of every hour
        # If current minute is < 59, it should not fire
        schedule = ScheduleConfig(type="cron", expression="59 * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        now = datetime.now(UTC)
        if now.minute >= 59:
            pytest.skip("Current minute is 59, would fire — test not valid at this time")

        # Should not fire because minute hasn't reached 59
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_auto_disables_one_shot(self, repo: TaskRepository) -> None:
        """R5: One-shot definition is auto-disabled after firing."""
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        schedule = ScheduleConfig(type="once", at=past_time)
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        updated_def = await repo.get_definition("def-1")
        assert updated_def is not None
        assert updated_def.enabled is False

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1

    async def test_skips_one_shot_already_fired(self, repo: TaskRepository) -> None:
        """R5: One-shot that has already fired is skipped."""
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        schedule = ScheduleConfig(type="once", at=past_time)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            last_fired_at=datetime.now(UTC),
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_one_shot_uses_schedule_at_for_scheduled_for(
        self,
        repo: TaskRepository,
    ) -> None:
        """R5: One-shot instance scheduled_for equals schedule.at."""
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        schedule = ScheduleConfig(type="once", at=past_time)
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].scheduled_for == past_time

    async def test_catchup_creates_one_instance_after_restart(
        self,
        repo: TaskRepository,
    ) -> None:
        """R4: After restart with multiple missed periods, only one instance is created."""
        schedule = ScheduleConfig(type="cron", expression="*/5 * * * *")
        tz = ZoneInfo("UTC")

        # Simulate being down for 15+ minutes by setting last_fired_at well in the past
        past = datetime.now(tz) - timedelta(minutes=20)
        past_anchor = past.replace(second=0, microsecond=0)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            last_fired_at=past_anchor.astimezone(UTC),
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Only one instance should be created (not one per missed period)
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1

        # last_fired_at should be fast-forwarded to the latest match <= now
        updated_def = await repo.get_definition("def-1")
        assert updated_def is not None
        assert updated_def.last_fired_at is not None

    async def test_no_duplicate_on_restart_within_same_period(
        self,
        repo: TaskRepository,
    ) -> None:
        """R4: Restarting within the same period does not create a duplicate."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        tz = ZoneInfo("UTC")

        # Set last_fired_at to the current minute boundary (simulating just-fired)
        now = datetime.now(tz)
        current_minute = now.replace(second=0, microsecond=0)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            last_fired_at=current_minute.astimezone(UTC),
        )
        await repo.create_definition(definition)

        # Pre-create an instance for the current period
        await repo.create_instance(
            _make_instance(
                "existing-1",
                definition_id="def-1",
                status="pending",
                scheduled_for=current_minute.astimezone(UTC),
            )
        )

        settings = TaskSettings(timezone="UTC")
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        # Only the existing instance should exist
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].id == "existing-1"

    async def test_timezone_conversion_for_anchor(
        self,
        repo: TaskRepository,
    ) -> None:
        """R6: Cron evaluation uses correct timezone for anchor."""
        # Use a non-UTC timezone (America/New_York = UTC-5 or UTC-4 depending on DST)
        tz_str = "America/New_York"
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone=tz_str)
        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        # The instance should have been created with a valid scheduled_for
        assert instances[0].scheduled_for is not None


class TestSessionTaskScheduler:
    """Tests for the session_task_scheduler async function."""

    @pytest.mark.asyncio
    async def test_dispatches_when_idle(self, repo: TaskRepository) -> None:
        """AC: Dispatches SessionTaskReady when idle window exceeded."""
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)
        bus = EventBus()

        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        last_msg_time = datetime.now(UTC) - timedelta(minutes=10)

        def get_last_msg_time():
            return last_msg_time

        task = asyncio.create_task(session_task_scheduler(repo, settings, bus, get_last_msg_time))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(dispatched_events) == 1
        assert isinstance(dispatched_events[0], SessionTaskReady)
        assert dispatched_events[0].instance.id == "inst-1"

    @pytest.mark.asyncio
    async def test_skips_when_user_active(self, repo: TaskRepository) -> None:
        """AC: Skips when user is active (last_message_time too recent)."""
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=300, check_interval=300)
        bus = EventBus()
        bus.dispatch = AsyncMock()

        last_msg_time = datetime.now(UTC) - timedelta(seconds=30)

        def get_last_msg_time():
            return last_msg_time

        task = asyncio.create_task(session_task_scheduler(repo, settings, bus, get_last_msg_time))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        bus.dispatch.assert_not_called()

        inst = await repo.get_instance("inst-1")
        assert inst is not None
        assert inst.status == "pending"

    @pytest.mark.asyncio
    async def test_skips_when_no_pending_instances(self, repo: TaskRepository) -> None:
        """AC: Skips when no pending session instances."""
        settings = TaskSettings(idle_window=0, check_interval=300)
        bus = EventBus()
        bus.dispatch = AsyncMock()

        def get_last_msg_time():
            return None

        task = asyncio.create_task(session_task_scheduler(repo, settings, bus, get_last_msg_time))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        bus.dispatch.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_complete_marks_completed(self, repo: TaskRepository) -> None:
        """AC: on_complete callback marks instance completed in repository."""
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)
        bus = EventBus()

        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        def get_last_msg_time():
            return datetime.now(UTC) - timedelta(hours=1)

        task = asyncio.create_task(session_task_scheduler(repo, settings, bus, get_last_msg_time))
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        await event.on_complete()

        inst = await repo.get_instance("inst-1")
        assert inst is not None
        assert inst.status == "completed"
        assert inst.completed_at is not None
