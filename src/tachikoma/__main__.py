"""CLI entry point for Tachikoma agent.

Supports both ``python -m tachikoma`` and the ``tachikoma`` console script
installed via ``uv tool install``. Bare invocation defaults to ``tachikoma run``.
"""

import asyncio
import sys
from pathlib import Path
from typing import Literal

from bubus import EventBus
from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from cyclopts import App
from loguru import logger
from rich.console import Console

from tachikoma.agent_defaults import AgentDefaults, merge_env
from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.boundary import SummaryProcessor
from tachikoma.config import SettingsManager
from tachikoma.context import CoreContextProcessor, context_hook
from tachikoma.coordinator import Coordinator
from tachikoma.database import Database, database_hook
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
from tachikoma.skills import SkillRegistry, SkillsContextProvider, skills_hook, watch_skills
from tachikoma.tasks import (
    TaskRepository,
    background_task_runner,
    create_task_tools_server,
    instance_generator,
    session_task_scheduler,
)
from tachikoma.tasks.hooks import tasks_hook
from tachikoma.telegram import TelegramChannel, telegram_hook
from tachikoma.workspace import workspace_hook

_log = logger.bind(component="main")

app = App()


def cli():
    """Entry point for [project.scripts]."""
    app()


@app.command
async def run(
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
    bootstrap.register("database", database_hook)
    bootstrap.register("git", git_hook)
    bootstrap.register("projects", projects_hook)
    bootstrap.register("skills", skills_hook)
    bootstrap.register("context", context_hook)
    bootstrap.register("memory", memory_hook)
    bootstrap.register("sessions", session_recovery_hook)
    bootstrap.register("tasks", tasks_hook)
    bootstrap.register("telegram", telegram_hook)

    try:
        await bootstrap.run()
    except BootstrapError as e:
        _log.error("Bootstrap failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)

    settings = settings_manager.settings

    # Retrieve the shared database and subsystem objects from bootstrap
    database: Database = bootstrap.extras["database"]
    registry = bootstrap.extras["session_registry"]
    task_repository: TaskRepository = bootstrap.extras["task_repository"]
    skill_registry: SkillRegistry = bootstrap.extras["skill_registry"]
    bus = EventBus()

    # Skills provider will be registered in pre-processing pipeline
    # (agents are now detected per-session via SkillsContextProvider)
    _log.info(
        "Startup complete: workspace={ws}, log_level={level}, channel={ch}",
        ws=settings.workspace.path,
        level=settings.logging.level,
        ch=settings.channel,
    )

    # Get the foundational context from the context hook (if available)
    foundational_context = bootstrap.extras.get("foundational_context")

    # Build AgentDefaults: merge hardcoded env with config env (collision = error)
    try:
        merged_env = merge_env(settings.agent.env)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    agent_defaults = AgentDefaults(
        cwd=settings.workspace.path,
        cli_path=settings.agent.cli_path,
        env=merged_env,
        model=settings.agent.sub_agent_model,
    )

    # Create and configure the session post-processing pipeline
    pipeline = PostProcessingPipeline(registry)
    pipeline.register(EpisodicProcessor(agent_defaults))
    pipeline.register(FactsProcessor(agent_defaults))
    pipeline.register(PreferencesProcessor(agent_defaults))
    pipeline.register(CoreContextProcessor(agent_defaults))
    pipeline.register(ProjectsProcessor(agent_defaults), phase=PRE_FINALIZE_PHASE)
    pipeline.register(GitProcessor(agent_defaults), phase=FINALIZE_PHASE)

    # Create and configure the pre-processing pipeline
    pre_pipeline = PreProcessingPipeline()
    pre_pipeline.register(MemoryContextProvider(agent_defaults))
    pre_pipeline.register(ProjectsContextProvider(workspace_path=settings.workspace.path))
    pre_pipeline.register(SkillsContextProvider(agent_defaults, skill_registry))

    # Create and configure the per-message post-processing pipeline
    msg_pipeline = MessagePostProcessingPipeline()
    msg_pipeline.register(SummaryProcessor(registry=registry, agent_defaults=agent_defaults))

    task_tools = create_task_tools_server(task_repository)

    console = Console()

    scheduler_tasks: list[asyncio.Task[None]] = []

    try:
        async with Coordinator(
            allowed_tools=settings.agent.allowed_tools,
            disallowed_tools=settings.agent.disallowed_tools,
            model=settings.agent.model,
            agent_defaults=agent_defaults,
            registry=registry,
            foundational_context=foundational_context,
            pipeline=pipeline,
            pre_pipeline=pre_pipeline,
            msg_pipeline=msg_pipeline,
            permission_mode="bypassPermissions",
            on_status=lambda msg: console.print(msg, style="dim italic grey50"),
            session_resume_window=settings.agent.session_resume_window,
            session_idle_timeout=settings.agent.session_idle_timeout,
            mcp_servers={"task-tools": task_tools},
        ) as coordinator:
            scheduler_tasks.append(
                asyncio.create_task(
                    instance_generator(task_repository, settings.tasks),
                    name="instance_generator",
                )
            )

            scheduler_tasks.append(
                asyncio.create_task(
                    session_task_scheduler(
                        task_repository,
                        settings.tasks,
                        bus,
                        lambda: coordinator.last_message_time,
                    ),
                    name="session_task_scheduler",
                )
            )

            scheduler_tasks.append(
                asyncio.create_task(
                    background_task_runner(
                        task_repository,
                        settings.tasks,
                        bus,
                        agent_defaults,
                        skill_registry,
                        registry,
                    ),
                    name="background_task_runner",
                )
            )

            scheduler_tasks.append(
                asyncio.create_task(
                    watch_skills(
                        settings.workspace.path / "skills",
                        skill_registry,
                        bus,
                    ),
                    name="skills_watcher",
                )
            )

            _log.info("Task schedulers started: tasks={count}", count=len(scheduler_tasks))

            # Dispatch based on channel setting
            if settings.channel == "telegram":
                if settings.telegram is None:
                    print(
                        "Telegram configuration is required when channel is 'telegram'",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                telegram_channel = TelegramChannel(coordinator, settings.telegram, bus=bus)
                await telegram_channel.run()
            else:
                # Default: REPL channel
                repl = Repl(
                    coordinator,
                    history_path=Path("/tmp/tachikoma_repl_history"),
                    bus=bus,
                )
                await repl.run()

    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        _log.error("Connection failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        # Cancel scheduler tasks
        for task in scheduler_tasks:
            task.cancel()

        # Wait for all tasks to complete
        if scheduler_tasks:
            results = await asyncio.gather(*scheduler_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                    _log.exception(
                        "Scheduler task {i} failed during shutdown: {err}",
                        i=i,
                        err=str(result),
                    )

        # Stop the event bus
        await bus.stop()

        # Dispose the shared database engine to prevent dangling connections
        if database is not None:
            await database.close()


@app.default
async def default_command() -> None:
    """Default command — delegates to run."""
    await run()


if __name__ == "__main__":
    cli()
