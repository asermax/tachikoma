"""Bootstrap hook for workflow subsystem.

Initializes the WorkflowStateRepository from the shared database
and stores it in the bootstrap extras for downstream consumers.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.workflows.repository import WorkflowStateRepository

_log = logger.bind(component="workflows")


async def workflows_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize workflow state repository.

    Retrieves the shared database from ctx.extras and creates a
    WorkflowStateRepository with its session factory.

    Keys written to ctx.extras:
        "workflow_repository" -> WorkflowStateRepository instance
    """
    database = ctx.extras["database"]

    repository = WorkflowStateRepository(database.session_factory)
    ctx.extras["workflow_repository"] = repository

    _log.debug("Workflow repository initialized")
