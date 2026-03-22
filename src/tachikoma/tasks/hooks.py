"""Bootstrap hook for the task subsystem.

Initializes the task repository, runs crash recovery, and creates the event bus.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="tasks")


async def tasks_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize task repository and event bus.

    Creates the TaskRepository, runs crash recovery, and creates the EventBus.
    Stores artifacts in ctx.extras for retrieval after bootstrap completes.

    Keys written to ctx.extras:
        "task_repository" -> TaskRepository instance
    """
    _log.info("Tasks hook started")

    data_path = ctx.settings_manager.settings.workspace.data_path

    # Create and initialize the task repository
    repository = TaskRepository(data_path / "tasks.db")
    await repository.initialize()

    # Run crash recovery: mark any running instances as failed
    count = await repository.mark_running_as_failed("system restart")
    if count > 0:
        _log.info("Crash recovery: marked {count} running instances as failed", count=count)

    ctx.extras["task_repository"] = repository

    _log.info("Tasks hook completed")
