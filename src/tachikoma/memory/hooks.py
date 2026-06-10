"""Bootstrap hook for memory directory initialization.

Creates the memories/ directory structure on first run (idempotent).
Also ensures MEMORY.md index files exist in facts/ and preferences/
directories, creating them via heavy rebuild if the directory already
has content.
"""

from loguru import logger

from tachikoma.agent_defaults import agent_defaults_from_settings
from tachikoma.bootstrap import BootstrapContext
from tachikoma.memory.index import run_index_rebuild

_log = logger.bind(component="memory")

# Indexable memory types — directories that should have a MEMORY.md index.
# Episodic memories are excluded (date-organized, searched differently).
_INDEXABLE_TYPES = ("facts", "preferences")


async def memory_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: create memories directory structure and index files.

    Creates memories/, memories/episodic/, memories/facts/,
    memories/preferences/, and memories/transcripts/ within the workspace
    path. For facts/ and preferences/, also ensures a MEMORY.md index
    exists — creating a header-only file for empty directories or
    triggering a full rebuild for directories with existing content.

    Idempotent — safe to call on every launch.

    Args:
        ctx: Bootstrap context with settings manager.
    """
    workspace_path = ctx.settings_manager.settings.workspace.path

    memories_root = workspace_path / "memories"
    episodic_path = memories_root / "episodic"
    facts_path = memories_root / "facts"
    preferences_path = memories_root / "preferences"
    transcripts_path = memories_root / "transcripts"

    # Create all directories idempotently
    memories_root.mkdir(parents=True, exist_ok=True)
    episodic_path.mkdir(exist_ok=True)
    facts_path.mkdir(exist_ok=True)
    preferences_path.mkdir(exist_ok=True)
    transcripts_path.mkdir(exist_ok=True)

    _log.info(
        "Memory directories initialized: root={root}",
        root=str(memories_root),
    )

    # Ensure MEMORY.md index files exist in indexable directories.
    # See DES-003: hook is idempotent — skips if index already present.
    for memory_type in _INDEXABLE_TYPES:
        directory = memories_root / memory_type
        index_path = directory / "MEMORY.md"

        if index_path.exists():
            _log.debug(
                "Index already exists: type={type}",
                type=memory_type,
            )
            continue

        # Check for existing .md files (excluding MEMORY.md itself).
        md_files = [f for f in directory.iterdir() if f.suffix == ".md" and f.name != "MEMORY.md"]

        if not md_files:
            # Empty directory — create header-only index.
            index_path.write_text("# Memory Index\n")
            _log.info(
                "Created empty index: type={type}",
                type=memory_type,
            )
        else:
            # Directory has content — trigger heavy rebuild.
            _log.info(
                "Index missing with {count} files, triggering rebuild: type={type}",
                count=len(md_files),
                type=memory_type,
            )
            agent_defaults = agent_defaults_from_settings(ctx.settings_manager.settings)
            await run_index_rebuild(agent_defaults, memory_type)
