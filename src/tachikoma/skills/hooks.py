"""Bootstrap hook for skills directory initialization and registry creation.

Creates the skills/ directory on first run (idempotent) and creates the
SkillRegistry with both built-in and workspace skill sources, shared
between the provider and watcher.
"""

from pathlib import Path

from loguru import logger

from tachikoma.bootstrap import BootstrapContext
from tachikoma.skills.registry import SkillRegistry

_log = logger.bind(component="skills")


async def skills_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create skills directory and shared registry.

    Creates workspace/skills/ directory within the workspace path (idempotent),
    resolves the built-in skills path, creates the SkillRegistry with both
    sources, and stores it in ctx.extras for downstream consumers (provider
    and filesystem watcher).

    Built-in skills are scanned first, workspace skills second — workspace
    skills completely replace built-in skills with the same name (last-wins).

    Args:
        ctx: Bootstrap context with settings manager and extras bag.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path
    skills_path = workspace_path / "skills"

    # Create workspace skills directory (idempotent)
    skills_path.mkdir(parents=True, exist_ok=True)

    # Resolve built-in skills path
    builtin_path = Path(__file__).parent / "builtin"
    skill_sources: list[Path] = []

    if builtin_path.exists():
        skill_sources.append(builtin_path)
    else:
        _log.warning(
            "Built-in skills directory not found: path={path}",
            path=str(builtin_path),
        )

    skill_sources.append(skills_path)

    # Create shared registry and expose via extras
    registry = SkillRegistry(skill_sources)
    ctx.extras["skill_registry"] = registry

    _log.debug(
        "Skills registry initialized: sources={count}, skills={skills}",
        count=len(skill_sources),
        skills=list(registry.skills.keys()),
    )
