"""CLI entry point for Tachikoma agent.

Supports both ``python -m tachikoma`` and the ``tachikoma`` console script
installed via ``uv tool install``. Bare invocation defaults to ``tachikoma run``.
"""

import asyncio
import sys
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from bubus import EventBus
from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError
from cyclopts import App
from loguru import logger

from tachikoma.agent_defaults import agent_defaults_from_settings
from tachikoma.bootstrap import Bootstrap, BootstrapError
from tachikoma.boundary import LastExchangeProcessor, SummaryProcessor
from tachikoma.buffer.buffer import Buffer
from tachikoma.buffer.factory import create_and_start_buffer
from tachikoma.config import SettingsManager
from tachikoma.context import CoreContextProcessor, context_hook
from tachikoma.coordinator import Coordinator
from tachikoma.database import Database, database_hook
from tachikoma.detached_processes import (
    ProcessRepository,
    create_detached_process_tools_server,
    detached_processes_hook,
    event_driven_watcher,
    polling_watcher,
)
from tachikoma.git import (
    DESTRUCTIVE_GIT_DENY_PATTERNS,
    GitProcessor,
    create_git_tools_server,
    git_hook,
)
from tachikoma.logging import logging_hook
from tachikoma.media import media_hook
from tachikoma.memory import (
    EpisodicProcessor,
    FactsProcessor,
    MemoryContextProvider,
    PreferencesProcessor,
    TranscriptArchiveProcessor,
    memory_hook,
)
from tachikoma.message_post_processing import MessagePostProcessingPipeline
from tachikoma.per_message_pre_processing import MessagePreProcessingPipeline
from tachikoma.post_processing import (
    FINALIZE_PHASE,
    PRE_FINALIZE_PHASE,
    PostProcessingPipeline,
    make_bash_deny_hook,
)
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
from tachikoma.workflows.cleanup import StaleWorkflowCleanupProcessor
from tachikoma.workflows.hooks import workflows_hook
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import create_workflow_tools_server
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
    bootstrap.register("git", git_hook)
    bootstrap.register("database", database_hook)
    bootstrap.register("projects", projects_hook)
    bootstrap.register("skills", skills_hook)
    bootstrap.register("context", context_hook)
    bootstrap.register("memory", memory_hook)
    bootstrap.register("sessions", session_recovery_hook)
    bootstrap.register("tasks", tasks_hook)
    bootstrap.register("detached_processes", detached_processes_hook)
    bootstrap.register("media", media_hook)
    bootstrap.register("workflows", workflows_hook)
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
    workflow_repository: WorkflowStateRepository = bootstrap.extras["workflow_repository"]
    process_repository: ProcessRepository = bootstrap.extras["process_repository"]
    detached_log_dir: Path = bootstrap.extras["detached_process_log_dir"]
    bus = EventBus()

    _log.info(
        "Startup complete: workspace={ws}, log_level={level}, channel={ch}",
        ws=settings.workspace.path,
        level=settings.logging.level,
        ch=settings.channel,
    )

    # Get the foundational context from the context hook (if available)
    foundational_context = bootstrap.extras.get("foundational_context")

    # Build AgentDefaults: merge auto-injected, config, and hardcoded env
    try:
        agent_defaults = agent_defaults_from_settings(settings)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    # Create and configure the session post-processing pipeline
    pipeline = PostProcessingPipeline(registry)
    pipeline.register(EpisodicProcessor(agent_defaults))
    pipeline.register(FactsProcessor(agent_defaults))
    pipeline.register(PreferencesProcessor(agent_defaults))
    pipeline.register(CoreContextProcessor(agent_defaults))
    pipeline.register(ProjectsProcessor(agent_defaults), phase=PRE_FINALIZE_PHASE)
    pipeline.register(
        StaleWorkflowCleanupProcessor(workflow_repository),
        phase=PRE_FINALIZE_PHASE,
    )
    pipeline.register(
        TranscriptArchiveProcessor(agent_defaults),
        phase=PRE_FINALIZE_PHASE,
    )
    pipeline.register(GitProcessor(agent_defaults), phase=FINALIZE_PHASE)

    # Create and configure the pre-processing pipeline (session-gated: projects)
    pre_pipeline = PreProcessingPipeline()
    pre_pipeline.register(ProjectsContextProvider(workspace_path=settings.workspace.path))

    # Create and configure the per-message pre-processing pipeline (skills, memory)
    msg_pre_pipeline = MessagePreProcessingPipeline()
    msg_pre_pipeline.register(SkillsContextProvider(agent_defaults, skill_registry))
    msg_pre_pipeline.register(MemoryContextProvider(agent_defaults))

    # Create and configure the per-message post-processing pipeline
    msg_pipeline = MessagePostProcessingPipeline()
    msg_pipeline.register(SummaryProcessor(registry=registry, agent_defaults=agent_defaults))
    msg_pipeline.register(LastExchangeProcessor(registry=registry))

    task_tools = create_task_tools_server(task_repository, ZoneInfo(settings.tasks.timezone))
    workflow_tools = create_workflow_tools_server(
        workflow_repository,
        skill_registry,
        settings.workspace.path,
    )
    detached_process_tools = create_detached_process_tools_server(
        process_repository,
        bus,
        detached_log_dir,
        ZoneInfo(settings.tasks.timezone),
    )
    git_tools = create_git_tools_server(settings.workspace.path, agent_defaults)

    # Shared deny hook: blocks destructive bash git commands on every
    # non-git-processor agent surface (main coordinator and task executor).
    destructive_git_deny_hook = make_bash_deny_hook(DESTRUCTIVE_GIT_DENY_PATTERNS)

    # Create channel before coordinator to extract capabilities. The buffer is
    # attached after coordinator startup (see below) since it depends on the
    # coordinator instance.
    if settings.channel == "telegram":
        if settings.telegram is None:
            print(
                "Telegram configuration is required when channel is 'telegram'",
                file=sys.stderr,
            )
            sys.exit(1)

        active_channel = TelegramChannel(
            settings.telegram, workspace_path=settings.workspace.path, bus=bus
        )
    else:
        active_channel = Repl(
            history_path=Path("/tmp/tachikoma_repl_history"),
            bus=bus,
        )

    # Register channel-provided skills
    for source_path in active_channel.get_skill_sources():
        skill_registry.add_source(source_path)

    # Merge channel MCP servers with task and workflow tools
    channel_mcp = active_channel.get_mcp_servers()
    all_mcp_servers = {
        "task-tools": task_tools,
        "workflow-tools": workflow_tools,
        "detached-process-tools": detached_process_tools,
        "git-tools": git_tools,
        **channel_mcp,
    }

    scheduler_tasks: list[asyncio.Task[None]] = []
    buffer: Buffer | None = None

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
            msg_pre_pipeline=msg_pre_pipeline,
            skill_registry=skill_registry,
            permission_mode="bypassPermissions",
            session_resume_window=settings.agent.session_resume_window,
            session_idle_timeout=settings.agent.session_idle_timeout,
            mcp_servers=all_mcp_servers,
            timezone=settings.tasks.timezone,
            bus=bus,
            hooks=[destructive_git_deny_hook],
        ) as coordinator:
            buffer = await create_and_start_buffer(
                bus=bus,
                coordinator=coordinator,
                settings=settings.buffer,
            )

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
                        buffer,
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
                        extra_mcp_servers={"git-tools": git_tools, "task-tools": task_tools},
                        hooks=[destructive_git_deny_hook],
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

            scheduler_tasks.append(
                asyncio.create_task(
                    event_driven_watcher(process_repository, bus, detached_log_dir),
                    name="detached_event_watcher",
                )
            )

            scheduler_tasks.append(
                asyncio.create_task(
                    polling_watcher(process_repository, bus, detached_log_dir),
                    name="detached_polling_watcher",
                )
            )

            _log.info("Task schedulers started: tasks={count}", count=len(scheduler_tasks))

            # Attach buffer so the channel can flush it during its own teardown
            active_channel.attach_buffer(buffer)

            # Start channel with coordinator
            await active_channel.run(coordinator)

    except (CLINotFoundError, CLIConnectionError, ProcessError) as e:
        _log.error("Connection failed: err={err}", err=str(e))
        print(str(e), file=sys.stderr)
        sys.exit(1)
    finally:
        # Buffer flush happens inside the channel's run() teardown so that the
        # coordinator and channel subscription are still alive. Here we only
        # cancel the loop task (channel may have been killed before flushing).
        if buffer is not None:
            try:
                await buffer.stop()
            except Exception:
                _log.exception("Buffer stop failed during shutdown")

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
