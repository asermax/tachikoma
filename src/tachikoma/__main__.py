"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

from tachikoma.bootstrap import Bootstrap, BootstrapError, workspace_hook
from tachikoma.config import SettingsManager
from tachikoma.coordinator import Coordinator
from tachikoma.repl import Repl
from tachikoma.sessions import session_recovery_hook


async def main() -> None:
    settings_manager = SettingsManager()

    bootstrap = Bootstrap(settings_manager)
    bootstrap.register("workspace", workspace_hook)
    bootstrap.register("sessions", session_recovery_hook)

    try:
        await bootstrap.run()
    except BootstrapError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    settings = settings_manager.settings

    # Retrieve the session objects created inside the recovery hook
    repository = bootstrap.extras["session_repository"]
    registry = bootstrap.extras["session_registry"]

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
            cwd=settings.workspace.path,
            registry=registry,
        ) as coordinator:
            repl = Repl(coordinator, history_path=settings.workspace.path / "repl_history")
            await repl.run()
    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        # Always dispose the engine to prevent dangling connections
        await repository.close()


asyncio.run(main())
