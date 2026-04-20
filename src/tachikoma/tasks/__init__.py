"""Task subsystem for scheduling and executing background tasks.

Provides:
- TaskRepository: Async CRUD for task definitions and instances
- TaskDefinition, TaskInstance: Domain dataclasses
- ScheduleConfig: Schedule configuration (cron or one-shot)
- TaskStatus, TaskType: Literal type aliases
- instance_generator_tick, session_task_scheduler_tick, one_shot_cleanup_tick:
  scheduler tick entry points
- BackgroundTaskRunner, BackgroundTaskExecutor, expired_waiter_sweep: background task execution
"""

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.executor import (
    BackgroundTaskExecutor,
    BackgroundTaskRunner,
    expired_waiter_sweep,
)
from tachikoma.tasks.model import (
    ScheduleConfig,
    TaskDefinition,
    TaskInstance,
    TaskStatus,
    TaskType,
)
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import (
    instance_generator_tick,
    one_shot_cleanup_tick,
    session_task_scheduler_tick,
)
from tachikoma.tasks.tools import create_task_tools_server

__all__ = [
    "BackgroundTaskExecutor",
    "BackgroundTaskRunner",
    "ScheduleConfig",
    "TaskDefinition",
    "TaskInstance",
    "TaskRepository",
    "TaskRepositoryError",
    "TaskStatus",
    "TaskType",
    "create_task_tools_server",
    "expired_waiter_sweep",
    "instance_generator_tick",
    "one_shot_cleanup_tick",
    "session_task_scheduler_tick",
]
