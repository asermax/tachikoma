"""Bootstrap hook for skills directory initialization.

Creates the skills/ directory on first run (idempotent) and
creates the shared SkillRegistry for use by the provider and watcher.
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.skills.registry import SkillRegistry

_log = logger.bind(component="skills")


async def skills_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create skills directory and shared registry.

    Creates workspace/skills/ directory within the workspace path.
    Creates the shared SkillRegistry and stores it in ctx.extras
    for use by SkillsContextProvider and the filesystem watcher.

    Idempotent — safe to call on every launch.

    Args:
        ctx: Bootstrap context with settings manager.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path

    skills_path = workspace_path / "skills"

    # Create directory if missing (unconditional, idempotent)
    skills_path.mkdir(parents=True, exist_ok=True)

    # Create shared registry (must happen on every launch, not just first run)
    registry = SkillRegistry(workspace_path)
    ctx.extras["skill_registry"] = registry

    _log.debug(
        "Skills subsystem initialized: path={path}, skills={count}",
        path=str(skills_path),
        count=len(registry.skills),
    )
