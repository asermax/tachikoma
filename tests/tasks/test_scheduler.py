"""Tests for task scheduler and instance generator."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from bubus import EventBus

from tachikoma.config import TaskSettings
from tachikoma.tasks.events import SessionTaskReady
from tachikoma.tasks.model import ScheduleConfig
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import instance_generator, session_task_scheduler

from .conftest import _make_definition, _make_instance


class TestInstanceGenerator:
    """Tests for the instance_generator async function."""

    @pytest.mark.asyncio
    async def test_creates_instance_when_cron_fires(self, repo: TaskRepository) -> None:
        """AC: Creates pending instance when cron schedule fires."""
        # Create a definition with a cron that fires every minute
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        # Create a mock settings with UTC timezone
        settings = TaskSettings(timezone="UTC")

        # Run one iteration of the generator
        task = asyncio.create_task(instance_generator(repo, settings))

        # Give it enough time to run one iteration
        await asyncio.sleep(0.2)

        # Cancel the task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Check that an instance was created
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].definition_id == "def-1"

    @pytest.mark.asyncio
    async def test_skips_disabled_definitions(self, repo: TaskRepository) -> None:
        """AC: Skips definitions that are disabled."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule, enabled=False)
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No instances should be created
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self, repo: TaskRepository) -> None:
        """AC: Skips when pending instance already exists."""
        schedule = ScheduleConfig(type="cron", expression="* * * * *")
        definition = _make_definition("def-1", schedule=schedule)
        await repo.create_definition(definition)

        # Create an existing pending instance
        instance = _make_instance("inst-1", definition_id="def-1", status="pending")
        await repo.create_instance(instance)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should still only have one instance
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1
        assert instances[0].id == "inst-1"

    @pytest.mark.asyncio
    async def test_auto_disables_one_shot(self, repo: TaskRepository) -> None:
        """AC: Auto-disables one-shot definitions after firing."""
        # Create a one-shot schedule that has already passed
        past_time = datetime.now(UTC) - timedelta(minutes=5)
        schedule = ScheduleConfig(type="once", at=past_time)
        definition = _make_definition("def-1", schedule=schedule, task_type="session")
        await repo.create_definition(definition)

        settings = TaskSettings(timezone="UTC")

        task = asyncio.create_task(instance_generator(repo, settings))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Check definition is disabled
        updated_def = await repo.get_definition("def-1")
        assert updated_def is not None
        assert updated_def.enabled is False

        # Check instance was created
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 1

    @pytest.mark.asyncio
    async def test_skips_one_shot_already_fired(self, repo: TaskRepository) -> None:
        """AC: Skips one-shot that has already fired (last_fired_at set)."""
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
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No new instance should be created
        instances = await repo.get_pending_instances("session")
        assert len(instances) == 0


class TestSessionTaskScheduler:
    """Tests for the session_task_scheduler async function."""

    @pytest.mark.asyncio
    async def test_dispatches_when_idle(self, repo: TaskRepository) -> None:
        """AC: Dispatches SessionTaskReady when idle window exceeded."""
        # Create a pending session instance
        instance = _make_instance(
            "inst-1",
            task_type="session",
            status="pending",
        )
        await repo.create_instance(instance)

        settings = TaskSettings(idle_window=0, check_interval=300)
        bus = EventBus()

        # Track dispatched events
        dispatched_events = []

        async def capture_dispatch(event):
            dispatched_events.append(event)

        bus.dispatch = AsyncMock(side_effect=capture_dispatch)

        # Mock last_message_time to be old (idle)
        last_msg_time = datetime.now(UTC) - timedelta(minutes=10)
        get_last_msg_time = lambda: last_msg_time

        task = asyncio.create_task(
            session_task_scheduler(repo, settings, bus, get_last_msg_time)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Check event was dispatched
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

        # Mock last_message_time to be recent (user active)
        last_msg_time = datetime.now(UTC) - timedelta(seconds=30)
        get_last_msg_time = lambda: last_msg_time

        task = asyncio.create_task(
            session_task_scheduler(repo, settings, bus, get_last_msg_time)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No event should be dispatched
        bus.dispatch.assert_not_called()

        # Instance should still be pending
        inst = await repo.get_instance("inst-1")
        assert inst is not None
        assert inst.status == "pending"

    @pytest.mark.asyncio
    async def test_skips_when_no_pending_instances(self, repo: TaskRepository) -> None:
        """AC: Skips when no pending session instances."""
        settings = TaskSettings(idle_window=0, check_interval=300)
        bus = EventBus()
        bus.dispatch = AsyncMock()
        get_last_msg_time = lambda: None

        task = asyncio.create_task(
            session_task_scheduler(repo, settings, bus, get_last_msg_time)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # No event should be dispatched
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

        get_last_msg_time = lambda: datetime.now(UTC) - timedelta(hours=1)

        task = asyncio.create_task(
            session_task_scheduler(repo, settings, bus, get_last_msg_time)
        )
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Get the dispatched event and call on_complete
        assert len(dispatched_events) == 1
        event = dispatched_events[0]
        await event.on_complete()

        # Check instance is now completed
        inst = await repo.get_instance("inst-1")
        assert inst is not None
        assert inst.status == "completed"
        assert inst.completed_at is not None
