"""Bootstrap hooks for the sessions package.

Registers crash recovery as an async bootstrap hook so that interrupted
sessions from previous runs are closed before normal operation resumes.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository

_log = logger.bind(component="sessions")


async def session_recovery_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize session repository and recover interrupted sessions.

    Creates the SessionRepository and SessionRegistry, runs crash recovery,
    then stores both on ctx.extras so __main__.py can retrieve them after
    bootstrap completes.

    Keys written to ctx.extras:
        "session_repository" -> SessionRepository instance
        "session_registry"   -> SessionRegistry instance
    """
    _log.info("Session recovery hook started")

    data_path = ctx.settings_manager.settings.workspace.data_path

    repository = SessionRepository(data_path / "sessions.db")
    await repository.initialize()

    registry = SessionRegistry(repository)
    await registry.recover_interrupted()

    ctx.extras["session_repository"] = repository
    ctx.extras["session_registry"] = registry

    _log.info("Session recovery hook completed")
