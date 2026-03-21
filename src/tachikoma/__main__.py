"""Entry point for Tachikoma agent (python -m tachikoma)."""

import sys
from pathlib import Path
from typing import Literal

from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from cyclopts import App
from loguru import logger
from rich.console import Console

from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.boundary import SummaryProcessor
from tachikoma.config import SettingsManager
from tachikoma.context import CoreContextProcessor, context_hook
from tachikoma.coordinator import Coordinator
from tachikoma.git import GitProcessor, git_hook
from tachikoma.logging import logging_hook
from tachikoma.memory import (
    EpisodicProcessor,
    FactsProcessor,
    MemoryContextProvider,
    PreferencesProcessor,
    memory_hook,
)
from tachikoma.message_post_processing import MessagePostProcessingPipeline
from tachikoma.post_processing import FINALIZE_PHASE, PRE_FINALIZE_PHASE, PostProcessingPipeline
from tachikoma.pre_processing import PreProcessingPipeline
from tachikoma.projects import ProjectsContextProvider, ProjectsProcessor, projects_hook
from tachikoma.repl import Repl
from tachikoma.sessions import session_recovery_hook
from tachikoma.skills import SkillRegistry, skills_hook
from tachikoma.telegram import TelegramChannel, telegram_hook
from tachikoma.workspace import workspace_hook

_log = logger.bind(component="main")

app = App()


@app.default
async def main(
    channel: Literal["repl", "telegram"] | None = None,
) -> None:
    """Run the Tachikoma agent.

    Args:
        channel: Communication channel to use (repl or telegram).
                 Defaults to 'repl'. Overrides TOML config if provided.
    """
    # Remove loguru's default stderr handler to prevent log messages
    # from leaking to the console before configure_logging() runs
    logger.remove()

    settings_manager = SettingsManager()

    # Apply CLI override if provided (runtime-only, no file write)
    if channel is not None:
        settings_manager.update_root("channel", channel)
        settings_manager.reload()

    bootstrap = Bootstrap(settings_manager)
    bootstrap.register("workspace", workspace_hook)
    bootstrap.register("logging", logging_hook)
    bootstrap.register("git", git_hook)
    bootstrap.register("projects", projects_hook)
    bootstrap.register("skills", skills_hook)
    bootstrap.register("context", context_hook)
    bootstrap.register("memory", memory_hook)
    bootstrap.register("sessions", session_recovery_hook)
    bootstrap.register("telegram", telegram_hook)

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

    # Create skill registry and discover agents
    skill_registry = SkillRegistry(settings.workspace.path)
    agents = skill_registry.get_agents()

    _log.info(
        "Startup complete: workspace={ws}, log_level={level}, channel={ch}, agents={agent_count}",
        ws=settings.workspace.path,
        level=settings.logging.level,
        ch=settings.channel,
        agent_count=len(agents),
    )

    # Get the system prompt from the context hook (if available)
    system_prompt = bootstrap.extras.get("system_prompt")

    cli_path = settings.agent.cli_path

    # Create and configure the session post-processing pipeline
    pipeline = PostProcessingPipeline()
    pipeline.register(EpisodicProcessor(cwd=settings.workspace.path, cli_path=cli_path))
    pipeline.register(FactsProcessor(cwd=settings.workspace.path, cli_path=cli_path))
    pipeline.register(PreferencesProcessor(cwd=settings.workspace.path, cli_path=cli_path))
    pipeline.register(CoreContextProcessor(cwd=settings.workspace.path, cli_path=cli_path))
    pipeline.register(
        ProjectsProcessor(cwd=settings.workspace.path, cli_path=cli_path),
        phase=PRE_FINALIZE_PHASE,
    )
    pipeline.register(
        GitProcessor(cwd=settings.workspace.path, cli_path=cli_path), phase=FINALIZE_PHASE,
    )

    # Create and configure the pre-processing pipeline
    pre_pipeline = PreProcessingPipeline()
    pre_pipeline.register(MemoryContextProvider(cwd=settings.workspace.path, cli_path=cli_path))
    pre_pipeline.register(ProjectsContextProvider(workspace_path=settings.workspace.path))

    # Create and configure the per-message post-processing pipeline
    msg_pipeline = MessagePostProcessingPipeline()
    msg_pipeline.register(
        SummaryProcessor(registry=registry, cwd=settings.workspace.path, cli_path=cli_path),
    )

    console = Console()

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            model=settings.agent.model,
            cwd=settings.workspace.path,
            registry=registry,
            system_prompt=system_prompt,
            pipeline=pipeline,
            pre_pipeline=pre_pipeline,
            msg_pipeline=msg_pipeline,
            permission_mode="bypassPermissions",
            env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"},
            on_status=lambda msg: console.print(msg, style="dim italic grey50"),
            agents=agents,
            cli_path=cli_path,
        ) as coordinator:
            # Dispatch based on channel setting
            if settings.channel == "telegram":
                if settings.telegram is None:
                    print(
                        "Telegram configuration is required when channel is 'telegram'",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                telegram_channel = TelegramChannel(coordinator, settings.telegram)
                await telegram_channel.run()
            else:
                # Default: REPL channel
                repl = Repl(coordinator, history_path=Path("/tmp/tachikoma_repl_history"))
                await repl.run()

    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        _log.error("Connection failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        # Always dispose the engine to prevent dangling connections
        if repository is not None:
            await repository.close()


app()
