"""Scheduling loops for task subsystem.

This module contains:
- instance_generator: async loop that evaluates task definitions and creates instances
- session_task_scheduler: async loop that dispatches ready session tasks onto the event bus
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from bubus import EventBus
from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger

from tachikoma.config import TaskSettings
from tachikoma.tasks.events import SessionTaskReady
from tachikoma.tasks.model import TaskInstance
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="task_scheduler")

# How often the instance generator checks for schedule matches
GENERATION_INTERVAL_SECONDS = 60


def _get_timezone(settings: TaskSettings) -> ZoneInfo:
    """Get the timezone for schedule evaluation."""
    if settings.timezone:
        try:
            return ZoneInfo(settings.timezone)
        except Exception:
            _log.warning(
                "Invalid timezone '{tz}', falling back to system",
                tz=settings.timezone,
            )
    # Default to system local timezone or UTC
    try:
        return ZoneInfo("localtime")
    except Exception:
        return ZoneInfo("UTC")


async def instance_generator(
    repository: TaskRepository,
    settings: TaskSettings,
) -> None:
    """Async loop that evaluates task definitions and creates instances.

    Runs every ~60 seconds. For each enabled definition:
    - Evaluate schedule against current time (cronsim for cron, datetime comparison for one-shot)
    - Check for existing pending/running instance (duplicate prevention)
    - Create pending instance if schedule fires and no duplicate exists
    - Auto-disable one-shot definitions after firing

    Args:
        repository: TaskRepository for persistence
        settings: TaskSettings with timezone and other config
    """
    tz = _get_timezone(settings)
    _log.info("Instance generator started with timezone: {tz}", tz=tz.key)

    while True:
        try:
            # Get current time in configured timezone for cron evaluation
            now_utc = datetime.now(UTC)
            now_tz = datetime.now(tz)

            # Query all enabled definitions
            definitions = await repository.list_enabled_definitions()

            for definition in definitions:
                try:
                    schedule = definition.schedule

                    # Determine if schedule should fire
                    should_fire = False
                    schedule_time = now_utc

                    if schedule.type == "cron" and schedule.expression:
                        # Cron schedule: evaluate using cronsim
                        try:
                            # If never fired before, use a past anchor to get next fire time
                            # If fired before, use last_fired_at as anchor
                            if definition.last_fired_at is None:
                                # Use an hour ago as anchor to ensure we get the next occurrence
                                # that could have fired recently
                                anchor = now_tz.replace(minute=0, second=0, microsecond=0)
                            else:
                                anchor = definition.last_fired_at

                            cron = CronSim(schedule.expression, anchor)
                            next_fire = next(cron)

                            # Check if next fire time has passed or is very close to now
                            # (within 1 minute tolerance for test timing)
                            if next_fire <= now_tz or (next_fire - now_tz).total_seconds() < 60:
                                should_fire = True
                                schedule_time = now_utc

                        except CronSimError as e:
                            _log.warning(
                                "Invalid cron expression for {name}: {expr} - {err}",
                                name=definition.name,
                                expr=schedule.expression,
                                err=e,
                            )
                            continue
                        except StopIteration:
                            # No more occurrences
                            continue

                    elif (
                        schedule.type == "once"
                        and schedule.at
                        and definition.last_fired_at is None
                        and schedule.at <= now_utc
                    ):
                        # One-shot: fire if target time passed and hasn't fired yet
                        should_fire = True
                        schedule_time = schedule.at

                    if not should_fire:
                        continue

                    # Duplicate prevention: check for existing pending/running instance
                    active = await repository.get_active_instance_for_definition(
                        definition.id
                    )
                    if active is not None:
                        _log.debug(
                            "Skipping {name} - already has active instance {inst_id}",
                            name=definition.name,
                            inst_id=active.id,
                        )
                        continue

                    # Create pending instance
                    instance = TaskInstance(
                        id=str(uuid4()),
                        definition_id=definition.id,
                        task_type=definition.task_type,
                        prompt=definition.prompt,
                        status="pending",
                        scheduled_for=schedule_time,
                        started_at=None,
                        completed_at=None,
                        result=None,
                        created_at=now_utc,
                    )
                    await repository.create_instance(instance)

                    _log.info(
                        "Created instance {inst_id} for {name} (type={task_type})",
                        inst_id=instance.id,
                        name=definition.name,
                        task_type=definition.task_type,
                    )

                    if schedule.type == "once":
                        await repository.update_definition(
                            definition.id, last_fired_at=now_utc, enabled=False,
                        )
                        _log.info(
                            "Auto-disabled one-shot definition {name}",
                            name=definition.name,
                        )
                    else:
                        await repository.update_definition(
                            definition.id, last_fired_at=now_utc,
                        )

                except Exception as exc:
                    _log.exception(
                        "Error processing definition {id}: {err}",
                        id=definition.id,
                        err=str(exc),
                    )
                    continue

        except asyncio.CancelledError:
            _log.info("Instance generator cancelled")
            raise

        except Exception as exc:
            _log.exception(
                "Instance generator loop error: {err}",
                err=str(exc),
            )

        # Sleep until next check
        try:
            await asyncio.sleep(GENERATION_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            _log.info("Instance generator stopped")
            raise


async def session_task_scheduler(
    repository: TaskRepository,
    settings: TaskSettings,
    bus: EventBus,
    get_last_message_time: Callable[[], datetime | None],
) -> None:
    """Async loop that dispatches ready session tasks onto the event bus.

    Runs every check_interval seconds. For pending session instances:
    - Idle gate: skip if user is active (last_message_time within idle_window)
    - Mark running, dispatch SessionTaskReady with on_complete callback

    Args:
        repository: TaskRepository for persistence
        settings: TaskSettings with idle_window and check_interval
        bus: EventBus for dispatching events
        get_last_message_time: Callable returning last message time from coordinator
    """
    _log.info(
        "Session task scheduler started (idle_window={idle}s, check_interval={check}s)",
        idle=settings.idle_window,
        check=settings.check_interval,
    )

    while True:
        try:
            # Query pending session instances
            pending_instances = await repository.get_pending_instances("session")

            if not pending_instances:
                _log.debug("No pending session instances")
            else:
                now_utc = datetime.now(UTC)
                last_message_time = get_last_message_time()

                for instance in pending_instances:
                    try:
                        # Check idle gate
                        if last_message_time is not None:
                            elapsed = (now_utc - last_message_time).total_seconds()
                            if elapsed < settings.idle_window:
                                _log.debug(
                                    "User is active (last message {elapsed}s ago), "
                                    "skipping instance {inst_id}",
                                    elapsed=int(elapsed),
                                    inst_id=instance.id,
                                )
                                continue

                        await repository.update_instance(
                            instance.id,
                            status="running",
                            started_at=now_utc,
                        )

                        async def on_complete(
                            inst_id: str = instance.id,
                        ) -> None:
                            await repository.update_instance(
                                inst_id,
                                status="completed",
                                completed_at=datetime.now(UTC),
                                result="Delivered successfully",
                            )

                        updated_instance = replace(instance, status="running", started_at=now_utc)
                        event = SessionTaskReady(
                            instance=updated_instance,
                            on_complete=on_complete,
                        )
                        await bus.dispatch(event)

                        _log.info(
                            "Dispatched SessionTaskReady for instance {inst_id}",
                            inst_id=instance.id,
                        )

                    except Exception as exc:
                        _log.exception(
                            "Error processing session instance {id}: {err}",
                            id=instance.id,
                            err=str(exc),
                        )
                        continue

        except asyncio.CancelledError:
            _log.info("Session task scheduler cancelled")
            raise

        except Exception as exc:
            _log.exception(
                "Session task scheduler loop error: {err}",
                err=str(exc),
            )

        # Sleep until next check
        try:
            await asyncio.sleep(settings.check_interval)
        except asyncio.CancelledError:
            _log.info("Session task scheduler stopped")
            raise
