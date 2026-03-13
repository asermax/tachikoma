"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.config import SettingsManager
from tachikoma.context import context_hook
from tachikoma.coordinator import Coordinator
from tachikoma.repl import Repl
from tachikoma.sessions import session_recovery_hook
from tachikoma.workspace import workspace_hook


async def main() -> None:
    settings_manager = SettingsManager()

    bootstrap = Bootstrap(settings_manager)
    bootstrap.register("workspace", workspace_hook)
    bootstrap.register("context", context_hook)
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

    # Get the system prompt from the context hook (if available)
    system_prompt = bootstrap.extras.get("system_prompt")

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
            cwd=settings.workspace.path,
            registry=registry,
            system_prompt=system_prompt,
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
