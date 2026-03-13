"""Bootstrap hook for memory directory initialization.

Creates the memories/ directory structure on first run (idempotent).
"""

from loguru import logger

from tachikoma.bootstrap import BootstrapContext

_log = logger.bind(component="memory")


async def memory_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create memories directory structure.

    Creates memories/, memories/episodic/, memories/facts/, and
    memories/preferences/ within the workspace path. Idempotent —
    safe to call on every launch.

    Args:
        ctx: Bootstrap context with settings manager.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path

    memories_root = workspace_path / "memories"
    episodic_path = memories_root / "episodic"
    facts_path = memories_root / "facts"
    preferences_path = memories_root / "preferences"

    # Create all directories idempotently
    memories_root.mkdir(parents=True, exist_ok=True)
    episodic_path.mkdir(exist_ok=True)
    facts_path.mkdir(exist_ok=True)
    preferences_path.mkdir(exist_ok=True)

    _log.info(
        "Memory directories initialized: root={root}",
        root=str(memories_root),
    )
