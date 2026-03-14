"""Entry point for Tachikoma agent (python -m tachikoma)."""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from loguru import logger
from rich.console import Console

from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.config import SettingsManager
from tachikoma.context import context_hook
from tachikoma.coordinator import Coordinator
from tachikoma.git import GitProcessor, git_hook
from tachikoma.logging import logging_hook
from tachikoma.memory import (
    EpisodicProcessor,
    FactsProcessor,
    PreferencesProcessor,
    memory_hook,
)
from tachikoma.post_processing import FINALIZE_PHASE, PostProcessingPipeline
from tachikoma.repl import Repl
from tachikoma.sessions import session_recovery_hook
from tachikoma.workspace import workspace_hook

_log = logger.bind(component="main")


async def main() -> None:
    settings_manager = SettingsManager()

    bootstrap = Bootstrap(settings_manager)
    bootstrap.register("workspace", workspace_hook)
    bootstrap.register("git", git_hook)
    bootstrap.register("logging", logging_hook)
    bootstrap.register("context", context_hook)
    bootstrap.register("memory", memory_hook)
    bootstrap.register("sessions", session_recovery_hook)

    try:
        await bootstrap.run()
    except BootstrapError as e:
        _log.error("Bootstrap failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)

    settings = settings_manager.settings

    # Retrieve the session objects created inside the recovery hook
    repository = bootstrap.extras["session_repository"]
    registry = bootstrap.extras["session_registry"]

    _log.info(
        "Startup complete: workspace={ws}, log_level={level}",
        ws=settings.workspace.path,
        level=settings.logging.level,
    )

    # Get the system prompt from the context hook (if available)
    system_prompt = bootstrap.extras.get("system_prompt")

    # Create and configure the post-processing pipeline
    pipeline = PostProcessingPipeline()
    pipeline.register(EpisodicProcessor(cwd=settings.workspace.path))
    pipeline.register(FactsProcessor(cwd=settings.workspace.path))
    pipeline.register(PreferencesProcessor(cwd=settings.workspace.path))
    pipeline.register(GitProcessor(cwd=settings.workspace.path), phase=FINALIZE_PHASE)

    console = Console()

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
            cwd=settings.workspace.path,
            registry=registry,
            system_prompt=system_prompt,
            pipeline=pipeline,
            permission_mode="bypassPermissions",
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            on_status=lambda msg: console.print(msg, style="dim italic grey50"),
        ) as coordinator:
            repl = Repl(coordinator, history_path=Path("/tmp/tachikoma_repl_history"))
            await repl.run()
    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        _log.error("Connection failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        # Always dispose the engine to prevent dangling connections
        await repository.close()


asyncio.run(main())
