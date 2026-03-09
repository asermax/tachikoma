"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

from tachikoma.config import load_settings
from tachikoma.coordinator import Coordinator
from tachikoma.repl import Repl


async def main() -> None:
    settings = load_settings()
    settings.workspace.path.mkdir(parents=True, exist_ok=True)

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
        ) as coordinator:
            repl = Repl(coordinator, history_path=settings.workspace.path / "repl_history")
            await repl.run()
    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


asyncio.run(main())
