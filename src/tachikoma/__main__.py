"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

from tachikoma.bootstrap import Bootstrap, BootstrapError, workspace_hook
from tachikoma.config import SettingsManager
from tachikoma.coordinator import Coordinator
from tachikoma.repl import Repl


async def main() -> None:
    settings_manager = SettingsManager()

    bootstrap = Bootstrap(settings_manager)
    bootstrap.register("workspace", workspace_hook)

    try:
        bootstrap.run()
    except BootstrapError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    settings = settings_manager.settings

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
            cwd=settings.workspace.path,
        ) as coordinator:
            repl = Repl(coordinator, history_path=settings.workspace.path / "repl_history")
            await repl.run()
    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


asyncio.run(main())
