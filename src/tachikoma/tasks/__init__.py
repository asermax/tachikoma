"""Task subsystem for scheduling and executing background tasks.

Provides:
- TaskRepository: Async CRUD for task definitions and instances
- TaskDefinition, TaskInstance: Domain dataclasses
- ScheduleConfig: Schedule configuration (cron or one-shot)
- TASK_STATUS, TASK_TYPE: Status and type constants
- SessionTaskReady, TaskNotification: Typed event classes for event bus
- instance_generator, session_task_scheduler: Async scheduling loops
- background_task_runner, BackgroundTaskExecutor: Background task execution
"""

from tachikoma.tasks.errors import TaskRepositoryError
from tachikoma.tasks.events import SessionTaskReady, TaskNotification
from tachikoma.tasks.executor import BackgroundTaskExecutor, background_task_runner
from tachikoma.tasks.model import (
    TASK_STATUS,
    TASK_TYPE,
    ScheduleConfig,
    TaskDefinition,
    TaskInstance,
    TaskStatus,
    TaskType,
)
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import instance_generator, session_task_scheduler

__all__ = [
    "TASK_STATUS",
    "TASK_TYPE",
    "BackgroundTaskExecutor",
    "ScheduleConfig",
    "SessionTaskReady",
    "TaskDefinition",
    "TaskInstance",
    "TaskNotification",
    "TaskRepository",
    "TaskRepositoryError",
    "TaskStatus",
    "TaskType",
    "background_task_runner",
    "instance_generator",
    "session_task_scheduler",
]
