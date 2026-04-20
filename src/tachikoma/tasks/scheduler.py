"""Tick entry points for task-subsystem scheduling.

Exposes tick functions driven by the central scheduler:
- ``instance_generator_tick``: evaluate definitions and create instances
- ``session_task_scheduler_tick``: enqueue ready session instances into the buffer
- ``one_shot_cleanup_tick``: delete expired one-shot definitions past retention

Each tick performs a single pass. Looping, sleeping, and cancellation are
handled by ``tachikoma.scheduler``.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from cronsim import CronSim
from cronsim.cronsim import CronSimError
from loguru import logger

from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.items import BufferedItem
from tachikoma.config import TaskSettings
from tachikoma.tasks.model import TaskDefinition, TaskInstance
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="task_scheduler")

# Cadence at which instance_generator_tick is driven by the central scheduler
GENERATION_INTERVAL_SECONDS = 60


async def _create_pending_instance(
    repository: TaskRepository,
    definition: TaskDefinition,
    scheduled_for: datetime,
    now_utc: datetime,
) -> TaskInstance:
    """Create a pending task instance, persist it, and log."""
    instance = TaskInstance(
        id=str(uuid4()),
        definition_id=definition.id,
        task_type=definition.task_type,
        prompt=definition.prompt,
        status="pending",
        scheduled_for=scheduled_for,
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

    return instance


def get_timezone(settings: TaskSettings) -> ZoneInfo:
    """Get the timezone for schedule evaluation.

    Settings are pre-validated at startup — timezone is always a valid IANA key.
    """
    return ZoneInfo(settings.timezone)


async def instance_generator_tick(
    repository: TaskRepository,
    settings: TaskSettings,
) -> None:
    """One pass of the instance generator.

    For each enabled definition:
    - Evaluate schedule against current time (cronsim for cron, datetime comparison for one-shot)
    - Period-aware duplicate check (pending/running/completed with matching scheduled_for)
    - Create pending instance if schedule fires and no duplicate exists
    - Auto-disable one-shot definitions after firing
    """
    tz = get_timezone(settings)
    now_utc = datetime.now(UTC)
    now_tz = datetime.now(tz)

    definitions = await repository.list_enabled_definitions()

    for definition in definitions:
        try:
            schedule = definition.schedule

            if schedule.type == "cron" and schedule.expression:
                try:
                    # last_fired_at is stored UTC, so convert to the evaluation tz
                    if definition.last_fired_at is None:
                        anchor_tz = now_tz.replace(
                            minute=0,
                            second=0,
                            microsecond=0,
                        ) - timedelta(seconds=1)
                    else:
                        anchor_tz = definition.last_fired_at.astimezone(tz)

                    next_fire = next(CronSim(schedule.expression, anchor_tz))

                    # Stale-cron prevention: if the match is before `since`,
                    # advance past `since` to find the next valid occurrence
                    since_tz = definition.since.astimezone(tz)
                    if next_fire <= since_tz:
                        advanced_anchor = since_tz + timedelta(seconds=1)
                        try:
                            next_fire = next(CronSim(schedule.expression, advanced_anchor))
                        except (CronSimError, StopIteration):
                            continue

                    if next_fire > now_tz:
                        continue

                    cron_match_utc = next_fire.astimezone(UTC)

                    active = await repository.get_active_instance_for_definition(
                        definition.id,
                        scheduled_for=cron_match_utc,
                    )
                    if active is not None:
                        _log.debug(
                            "Duplicate suppressed for {name} — period {match} already covered",
                            name=definition.name,
                            match=cron_match_utc.isoformat(),
                        )
                        continue

                    await _create_pending_instance(
                        repository,
                        definition,
                        cron_match_utc,
                        now_utc,
                    )

                    # Advance last_fired_at so next cycle's CronSim anchor
                    # produces a future time, preventing catch-up duplicates
                    await repository.update_definition(
                        definition.id,
                        last_fired_at=now_utc,
                    )

                except CronSimError as e:
                    _log.warning(
                        "Invalid cron expression for {name}: {expr} - {err}",
                        name=definition.name,
                        expr=schedule.expression,
                        err=e,
                    )
                    continue
                except StopIteration:
                    continue

            elif (
                schedule.type == "once"
                and schedule.at
                and definition.last_fired_at is None
                and schedule.at <= now_utc
            ):
                active = await repository.get_active_instance_for_definition(
                    definition.id,
                )
                if active is not None:
                    _log.debug(
                        "Skipping {name} - already has active instance {inst_id}",
                        name=definition.name,
                        inst_id=active.id,
                    )
                    continue

                await _create_pending_instance(
                    repository,
                    definition,
                    schedule.at,
                    now_utc,
                )

                await repository.update_definition(
                    definition.id,
                    last_fired_at=now_utc,
                    enabled=False,
                )
                _log.info(
                    "Auto-disabled one-shot definition {name}",
                    name=definition.name,
                )

        except Exception as exc:
            _log.exception(
                "Error processing definition {id}: {err}",
                id=definition.id,
                err=str(exc),
            )
            continue


async def session_task_scheduler_tick(
    repository: TaskRepository,
    settings: TaskSettings,  # noqa: ARG001 — kept for signature symmetry with other ticks
    buffer: Buffer,
) -> None:
    """One pass of the session task scheduler.

    Enqueues pending session instances into the priority buffer and marks
    them running with an on-delivered callback that completes them.
    """
    pending_instances = await repository.get_pending_instances("session")

    if not pending_instances:
        _log.debug("No pending session instances")
        return

    now_utc = datetime.now(UTC)

    for instance in pending_instances:
        try:
            await repository.update_instance(
                instance.id,
                status="running",
                started_at=now_utc,
            )

            async def on_complete(inst_id: str = instance.id) -> None:
                await repository.update_instance(
                    inst_id,
                    status="completed",
                    completed_at=datetime.now(UTC),
                    result="Delivered successfully",
                )

            updated_instance = replace(instance, status="running", started_at=now_utc)
            item = BufferedItem.from_session_instance(
                updated_instance,
                on_delivered=on_complete,
            )

            try:
                await buffer.enqueue(item)
            except Exception:
                # Roll back the running transition so the next tick retries —
                # otherwise the row is stuck running with no executor attached
                await repository.update_instance(
                    instance.id,
                    status="pending",
                    started_at=None,
                )
                raise

            _log.info(
                "Enqueued session task into buffer: inst_id={id}",
                id=instance.id,
            )

        except Exception as exc:
            _log.exception(
                "Error processing session instance {id}: {err}",
                id=instance.id,
                err=str(exc),
            )
            continue


async def one_shot_cleanup_tick(
    repository: TaskRepository,
    settings: TaskSettings,
) -> None:
    """Delete one-shot definitions past the configured retention window."""
    await repository.cleanup_expired_one_shot_definitions(settings.cleanup_retention_hours)
