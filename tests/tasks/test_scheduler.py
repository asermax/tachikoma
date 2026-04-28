"""Tests for task scheduler and instance generator.

See: docs/delta-specs/DLT-090.md (R0–R6 acceptance criteria)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from cronsim import CronSim

from tachikoma.buffer.items import BufferedItem
from tachikoma.config import TaskSettings
from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import (
    instance_generator_tick,
    session_task_scheduler_tick,
)

from .conftest import _make_definition, _make_instance


def _cron_first_match(expression: str, tz: ZoneInfo) -> datetime:
    """Compute the first cron match time that the generator would fire for now.

    Uses the same anchor logic as the generator (start-of-hour minus 1s when no last_fired_at).
    """
    now_tz = datetime.now(tz)
    anchor_tz = now_tz.replace(minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    return next(CronSim(expression, anchor_tz)).astimezone(UTC)


class TestInstanceGenerator:
    """Tests for the instance_generator async function."""

    async def test_creates_instance_when_cron_fires(self, repo: TaskRepository) -> None:
        """R0: Creates pending instance when cron schedule fires."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].definition_id == "def-1"

    async def test_scheduled_for_is_cron_match_time(self, repo: TaskRepository) -> None:
        """R0: Instance scheduled_for equals the cron match time, not wall-clock time."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        await instance_generator_tick(repo, TaskSettings(timezone="UTC"))

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

        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

        # A new instance should be created for the current period
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].id != "old-1"

    async def test_no_early_firing_before_cron_boundary(
        self, repo: TaskRepository, time_machine
    ) -> None:
        """R1: Generator does not fire when next cron time hasn't arrived yet."""
        # Freeze the clock well before minute 59 so the "59 * * * *" cron cannot fire.
        # tick=False keeps the instant stable — a drifting clock could cross minute 59
        # during asyncio.sleep and re-introduce flakiness.
        time_machine.move_to(datetime(2026, 4, 15, 12, 30, tzinfo=UTC), tick=False)

        schedule = ScheduleConfig(type="cron", expression="59 * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        await instance_generator_tick(repo, settings)

        # Should not fire because frozen minute (30) hasn't reached 59
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_auto_disables_one_shot(self, repo: TaskRepository) -> None:
        """R5: One-shot definition is auto-disabled after firing."""
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        schedule = ScheduleConfig(type="once", at=past_time)
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        await instance_generator_tick(repo, settings)

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

        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

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
        await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        # The instance should have been created with a valid scheduled_for
        assert instances[0].scheduled_for is not None

    async def test_fires_for_hour_boundary_cron_with_no_last_fired_at(
        self,
        repo: TaskRepository,
    ) -> None:
        """AC1: Hour-boundary cron fires when last_fired_at is NULL and time has passed."""
        # 2026-04-05 is a Sunday. Cron `0 13 * * 0` fires at 13:00 on Sundays.
        tz = ZoneInfo("UTC")
        sunday_13_00_30 = datetime(2026, 4, 5, 13, 0, 30, tzinfo=tz)
        sunday_12_00 = datetime(2026, 4, 5, 12, 0, 0, tzinfo=tz)

        schedule = ScheduleConfig(type="cron", expression="0 13 * * 0")
        definition = _make_definition("def-1", schedule=schedule, since=sunday_12_00)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        def mock_now(tz_arg):
            return sunday_13_00_30 if tz_arg else datetime.now()

        with patch("tachikoma.tasks.scheduler.datetime") as mock_dt:
            mock_dt.now = mock_now
            mock_dt.UTC = UTC
            await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].scheduled_for == datetime(2026, 4, 5, 13, 0, 0, tzinfo=tz)

    async def test_does_not_fire_before_hour_boundary_cron(
        self,
        repo: TaskRepository,
    ) -> None:
        """AC2: Hour-boundary cron does not fire before the match time."""
        tz = ZoneInfo("UTC")
        sunday_12_59_30 = datetime(2026, 4, 5, 12, 59, 30, tzinfo=tz)

        schedule = ScheduleConfig(type="cron", expression="0 13 * * 0")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        def mock_now(tz_arg):
            return sunday_12_59_30 if tz_arg else datetime.now()

        with patch("tachikoma.tasks.scheduler.datetime") as mock_dt:
            mock_dt.now = mock_now
            mock_dt.UTC = UTC
            await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_non_hour_boundary_cron_unchanged_with_null_last_fired_at(
        self,
        repo: TaskRepository,
    ) -> None:
        """AC3: Non-hour-boundary cron behavior unchanged by the fix."""
        tz = ZoneInfo("UTC")
        sunday_13_30_30 = datetime(2026, 4, 5, 13, 30, 30, tzinfo=tz)
        sunday_12_00 = datetime(2026, 4, 5, 12, 0, 0, tzinfo=tz)

        schedule = ScheduleConfig(type="cron", expression="30 13 * * 0")
        definition = _make_definition("def-1", schedule=schedule, since=sunday_12_00)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        def mock_now(tz_arg):
            return sunday_13_30_30 if tz_arg else datetime.now()

        with patch("tachikoma.tasks.scheduler.datetime") as mock_dt:
            mock_dt.now = mock_now
            mock_dt.UTC = UTC
            await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].scheduled_for == datetime(2026, 4, 5, 13, 30, 0, tzinfo=tz)

    async def test_hour_boundary_cron_with_last_fired_at(
        self,
        repo: TaskRepository,
    ) -> None:
        """AC4: Non-NULL last_fired_at with hour-boundary cron still fires correctly."""
        tz = ZoneInfo("UTC")
        sunday_13_00_30 = datetime(2026, 4, 5, 13, 0, 30, tzinfo=tz)
        prev_sunday_13_00 = datetime(2026, 3, 29, 13, 0, 0, tzinfo=UTC)
        prev_sunday_12_00 = datetime(2026, 3, 29, 12, 0, 0, tzinfo=tz)

        schedule = ScheduleConfig(type="cron", expression="0 13 * * 0")
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            last_fired_at=prev_sunday_13_00,
            since=prev_sunday_12_00,
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        def mock_now(tz_arg):
            return sunday_13_00_30 if tz_arg else datetime.now()

        with patch("tachikoma.tasks.scheduler.datetime") as mock_dt:
            mock_dt.now = mock_now
            mock_dt.UTC = UTC
            await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].scheduled_for == datetime(2026, 4, 5, 13, 0, 0, tzinfo=tz)


class TestStaleCronPrevention:
    """Tests for DLT-102: since-based stale cron prevention."""

    async def test_no_fire_when_cron_match_before_since_on_create(
        self,
        repo: TaskRepository,
        time_machine,
    ) -> None:
        """AC1: Newly created task with since after the cron match does not fire."""
        # Freeze at 8:05 AM — cron match would be 8:00 AM, but since = 8:05 AM
        tz = ZoneInfo("UTC")
        now = datetime(2026, 4, 5, 8, 5, 0, tzinfo=tz)
        time_machine.move_to(now, tick=False)

        schedule = ScheduleConfig(type="cron", expression="0 8 * * *")
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            since=now,
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_no_fire_when_cron_match_before_since_on_update(
        self,
        repo: TaskRepository,
        time_machine,
    ) -> None:
        """AC2: Updated task with since after the cron match does not fire."""
        tz = ZoneInfo("UTC")
        # Cron fires at minute 30; freeze at 12:00 (after the 11:30 match)
        now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=tz)
        time_machine.move_to(now, tick=False)

        schedule = ScheduleConfig(type="cron", expression="30 * * * *")
        # since is after the 11:30 match but before the 12:30 match
        since_time = datetime(2026, 4, 5, 11, 45, 0, tzinfo=tz)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            since=since_time,
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_advance_past_since_finds_next_occurrence(
        self,
        repo: TaskRepository,
        time_machine,
    ) -> None:
        """AC5: Stale match is advanced past since to find the next valid occurrence."""
        tz = ZoneInfo("UTC")
        # Freeze at 12:05 — cron fires at :00, since is at 12:03
        # First match from start-of-hour anchor is 12:00 (stale, before since)
        # Advanced match should be 13:00 (future, skip)
        now = datetime(2026, 4, 5, 12, 5, 0, tzinfo=tz)
        time_machine.move_to(now, tick=False)

        schedule = ScheduleConfig(type="cron", expression="0 * * * *")
        since_time = datetime(2026, 4, 5, 12, 3, 0, tzinfo=tz)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            since=since_time,
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        await instance_generator_tick(repo, settings)

        # No instance because 13:00 hasn't arrived yet
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    async def test_advance_past_since_fires_if_next_match_passed(
        self,
        repo: TaskRepository,
        time_machine,
    ) -> None:
        """AC5: Advanced match fires if it has already passed."""
        tz = ZoneInfo("UTC")
        # Freeze at 13:05 — cron fires at :00 and :30
        # since is at 12:03, so the 12:00 match is stale
        # Advanced match is 13:00, which has passed → fire
        now = datetime(2026, 4, 5, 13, 5, 0, tzinfo=tz)
        time_machine.move_to(now, tick=False)

        schedule = ScheduleConfig(type="cron", expression="0 * * * *")
        since_time = datetime(2026, 4, 5, 12, 3, 0, tzinfo=tz)
        definition = _make_definition(
            "def-1",
            schedule=schedule,
            since=since_time,
        )
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")
        await instance_generator_tick(repo, settings)

        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        # Should fire for 13:00 (the advanced match), not 12:00 (the stale one)
        assert instances[0].scheduled_for == datetime(2026, 4, 5, 13, 0, 0, tzinfo=tz)


class TestSessionTaskScheduler:
    """Tests for the session_task_scheduler async function."""

    @pytest.mark.asyncio
    async def test_enqueues_pending_instance(self, repo: TaskRepository) -> None:
        """AC: Enqueues BufferedItem when pending instance exists."""
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)

        enqueued_items: list[BufferedItem] = []

        mock_buffer = AsyncMock()
        mock_buffer.enqueue = AsyncMock(side_effect=lambda item: enqueued_items.append(item))

        await session_task_scheduler_tick(repo, settings, mock_buffer)

        assert len(enqueued_items) == 1
        assert enqueued_items[0].kind == "session_task"
        assert enqueued_items[0].priority == 2  # NORMAL
        assert enqueued_items[0].metadata["instance"].id == "inst-1"

    @pytest.mark.asyncio
    async def test_skips_when_no_pending_instances(self, repo: TaskRepository) -> None:
        """AC: Skips when no pending session instances."""
        settings = TaskSettings(idle_window=0, check_interval=300)

        mock_buffer = AsyncMock()

        await session_task_scheduler_tick(repo, settings, mock_buffer)

        mock_buffer.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_delivered_marks_completed(self, repo: TaskRepository) -> None:
        """AC: on_delivered callback marks instance completed in repository."""
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)

        enqueued_items: list[BufferedItem] = []

        mock_buffer = AsyncMock()
        mock_buffer.enqueue = AsyncMock(side_effect=lambda item: enqueued_items.append(item))

        await session_task_scheduler_tick(repo, settings, mock_buffer)

        assert len(enqueued_items) == 1
        item = enqueued_items[0]
        assert item.on_delivered is not None
        await item.on_delivered()

        inst = await repo.get_instance("inst-1")
        assert inst is not None
        assert inst.status == "completed"
        assert inst.completed_at is not None


class TestSessionTaskSchedulerPinnedSkills:
    """Tests for pinned skills propagation in session_task_scheduler (DLT-117)."""

    @pytest.mark.asyncio
    async def test_sets_pinned_skills_metadata(
        self, repo: TaskRepository
    ) -> None:
        """DLT-117: BufferedItem gets pinned_skills from definition."""
        defn = _make_definition(
            "def-skills",
            task_type="session",
            skills=("research", "planning"),
        )
        await repo.create_definition(defn)

        # Create a pending instance linked to the definition
        instance = _make_instance(
            "inst-1",
            definition_id="def-skills",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)
        enqueued_items: list[BufferedItem] = []
        mock_buffer = AsyncMock()
        mock_buffer.enqueue = AsyncMock(side_effect=lambda item: enqueued_items.append(item))

        await session_task_scheduler_tick(repo, settings, mock_buffer)

        assert len(enqueued_items) == 1
        assert enqueued_items[0].metadata["pinned_skills"] == ["research", "planning"]

    @pytest.mark.asyncio
    async def test_no_pinned_skills_metadata_when_empty(
        self, repo: TaskRepository
    ) -> None:
        """DLT-117: No pinned_skills metadata when definition has no skills."""
        defn = _make_definition("def-noskills", task_type="session")
        await repo.create_definition(defn)

        instance = _make_instance(
            "inst-2",
            definition_id="def-noskills",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)
        enqueued_items: list[BufferedItem] = []
        mock_buffer = AsyncMock()
        mock_buffer.enqueue = AsyncMock(side_effect=lambda item: enqueued_items.append(item))

        await session_task_scheduler_tick(repo, settings, mock_buffer)

        assert len(enqueued_items) == 1
        assert "pinned_skills" not in enqueued_items[0].metadata
