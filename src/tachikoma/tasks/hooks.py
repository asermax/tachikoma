"""Bootstrap hook for the task subsystem.

Initializes the task repository and runs crash recovery.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.database import Database
from tachikoma.tasks.repository import TaskRepository

_log = logger.bind(component="tasks")


async def tasks_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize task repository and run crash recovery.

    Retrieves the shared Database from ctx.extras, creates the
    TaskRepository, and runs crash recovery. Stores the repository
    in ctx.extras for retrieval after bootstrap completes.

    Keys written to ctx.extras:
        "task_repository" -> TaskRepository instance
    """
    _log.info("Tasks hook started")

    database: Database = ctx.extras["database"]

    repository = TaskRepository(database.session_factory)

    # Run crash recovery: mark any running instances as failed
    count = await repository.mark_running_as_failed("system restart")
    if count > 0:
        _log.info("Crash recovery: marked {count} running instances as failed", count=count)

    ctx.extras["task_repository"] = repository

    _log.info("Tasks hook completed")
