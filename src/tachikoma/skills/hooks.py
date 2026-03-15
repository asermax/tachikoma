"""Bootstrap hook for skills directory initialization.

Creates the skills/ directory on first run (idempotent).
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="skills")


async def skills_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create skills directory.

    Creates workspace/skills/ directory within the workspace path.
    Idempotent — safe to call on every launch.

    Args:
        ctx: Bootstrap context with settings manager.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path

    skills_path = workspace_path / "skills"

    if skills_path.exists():
        _log.debug("Skills directory already exists: path={path}", path=str(skills_path))
        return

    skills_path.mkdir(parents=True, exist_ok=True)
    _log.info("Skills directory created: path={path}", path=str(skills_path))
