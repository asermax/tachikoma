"""Bootstrap hook for the update checker subsystem."""

from loguru import logger

from tachikoma.app_state import AppStateRepository
from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="updates")


async def updates_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create AppStateRepository for update dedup.

    Keys written to ctx.extras:
        "app_state_repository" -> AppStateRepository instance
    """
    _log.info("Updates hook started")

    database = ctx.extras["database"]
    repository = AppStateRepository(database.session_factory)
    ctx.extras["app_state_repository"] = repository

    _log.info("Updates hook completed")
