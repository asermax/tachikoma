"""Bootstrap hooks for the sessions package.

Registers crash recovery as an async bootstrap hook so so that interrupted
sessions from previous runs are closed before normal operation resumes.
"""

from datetime import timedelta

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.database import Database
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository

_log = logger.bind(component="sessions")


async def session_recovery_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: initialize session repository and recover interrupted sessions.

    Retrieves the shared Database from ctx.extras, creates the
    SessionRepository and SessionRegistry, runs crash recovery, then
    stores both on ctx.extras for __main__.py retrieval.

    Keys written to ctx.extras:
        "session_repository" -> SessionRepository instance
        "session_registry"   -> SessionRegistry instance
    """
    _log.info("Session recovery hook started")

    database: Database = ctx.extras["database"]

    repository = SessionRepository(database.session_factory)

    settings = ctx.settings_manager.settings
    max_session_age = timedelta(seconds=settings.agent.session_resume_window)
    registry = SessionRegistry(repository, max_session_age=max_session_age)

    await registry.recover_interrupted()

    ctx.extras["session_repository"] = repository
    ctx.extras["session_registry"] = registry

    _log.info("Session recovery hook completed")
