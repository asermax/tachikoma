"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

from tachikoma.coordinator import Coordinator
from tachikoma.repl import Repl


async def main() -> None:
    Path.home().joinpath(".tachikoma").mkdir(exist_ok=True)

    try:
        async with Coordinator(allowed_tools=["Read", "Glob", "Grep"]) as coordinator:
            repl = Repl(coordinator)
            await repl.run()
    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


asyncio.run(main())
