"""Bootstrap hook for plugin reconciliation and manager creation.

Runs after ``git_hook`` and before ``skills_hook``. Creates the plugin install
directory, runs reconciliation (materialize declared plugins), discovers loaded
plugins, and constructs the ``PluginManager`` — all stored in ``ctx.extras``
for downstream hooks and ``__main__`` wiring.
"""

from __future__ import annotations

from pathlib import Path

from bubus import EventBus
from loguru import logger

from tachikoma.agent_defaults import agent_defaults_from_settings
from tachikoma.bootstrap import BootstrapContext
from tachikoma.plugins.loader import discover
from tachikoma.plugins.manager import PluginManager
from tachikoma.plugins.reconciler import reconcile
from tachikoma.plugins.state import PluginStateRepository

_log = logger.bind(component="plugins")

_GITIGNORE_PLUGIN_ENTRY = ".tachikoma/plugins/\n"


def _ensure_gitignore_entry(workspace_path: Path, entry: str) -> None:
    """Append *entry* to the workspace .gitignore if not already present."""
    gitignore = workspace_path / ".gitignore"
    content = gitignore.read_text() if gitignore.exists() else ""

    if entry in content:
        return

    separator = "" if not content or content.endswith("\n") else "\n"
    content += separator + entry
    gitignore.write_text(content)


async def plugins_hook(ctx: BootstrapContext) -> None:
    """Bootstrap hook: reconcile configured plugins and build the manager.

    Steps:
        1. Create ``.tachikoma/plugins/`` and ``.staging`` directories.
        2. Append gitignore entry (idempotent).
        3. Create PluginStateRepository from database session.
        4. Reconcile declared plugins with on-disk state (first-time-only).
        5. Discover loaded plugins.
        6. Construct PluginManager with state_repo.
        7. Store manager, state_repo, and skill paths in ``ctx.extras``.

    Pre-condition:
        ``ctx.extras["event_bus"]`` and ``ctx.extras["database"]`` must be
        populated before this hook runs.
    """
    settings = ctx.settings_manager.settings
    workspace_path = settings.workspace.path

    install_dir = workspace_path / ".tachikoma" / "plugins"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / ".staging").mkdir(parents=True, exist_ok=True)

    _ensure_gitignore_entry(workspace_path, _GITIGNORE_PLUGIN_ENTRY)

    # Create state repository from database session.
    database = ctx.extras.get("database")
    if database is None:
        msg = "plugins_hook requires ctx.extras['database'] to be populated before bootstrap.run()."
        raise RuntimeError(msg)
    state_repo = PluginStateRepository(database.session_factory)

    report = await reconcile(workspace_path, settings.plugins, state_repo)
    failed = [o for o in report.outcomes if o.status == "failed"]
    if failed:
        _log.warning(
            "Plugin reconciliation had failures: failures={failures}",
            failures=[f"{o.alias}: {o.diagnostic}" for o in failed],
        )

    agent_defaults = agent_defaults_from_settings(settings)

    loaded_plugins = discover(
        install_dir,
        report,
        settings.plugins,
        agent_defaults=agent_defaults,
    )

    bus: EventBus | None = ctx.extras.get("event_bus")
    if bus is None:
        msg = (
            "plugins_hook requires ctx.extras['event_bus'] to be populated "
            "before bootstrap.run(). The EventBus must be hoisted above "
            "bootstrap.run() in __main__.py."
        )
        raise RuntimeError(msg)

    manager = PluginManager(
        settings_manager=ctx.settings_manager,
        bus=bus,
        workspace_path=workspace_path,
        loaded={p.alias: p for p in loaded_plugins},
        state_repo=state_repo,
        agent_defaults=agent_defaults,
    )

    ctx.extras["plugin_manager"] = manager
    ctx.extras["state_repo"] = state_repo
    ctx.extras["plugin_skill_paths"] = [
        (p.alias, skill_dir)
        for p in loaded_plugins
        if p.status == "loaded" and p.manifest is not None
        for skill_dir in p.manifest.skill_dirs
    ]

    _log.info(
        "Plugins initialized: total={total} loaded={loaded}",
        total=len(loaded_plugins),
        loaded=sum(1 for p in loaded_plugins if p.status == "loaded"),
    )
