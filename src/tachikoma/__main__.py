"""CLI entry point for Tachikoma agent.

Supports both ``python -m tachikoma`` and the ``tachikoma`` console script
installed via ``uv tool install``. Bare invocation defaults to ``tachikoma run``.
"""

import asyncio
import os
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
    context_maintenance_tick,
    episodic_maintenance_tick,
    facts_maintenance_tick,
    memory_hook,
    preferences_maintenance_tick,
)
from tachikoma.message_post_processing import MessagePostProcessingPipeline
from tachikoma.notifications import dispatch_notification
from tachikoma.per_message_pre_processing import MessagePreProcessingPipeline
from tachikoma.plugins.hooks import plugins_hook
from tachikoma.plugins.tools import create_plugin_tools_server
from tachikoma.plugins.updater import run_daily_git_check
from tachikoma.post_processing import (
    PostProcessingPipeline,
    make_bash_deny_hook,
)
from tachikoma.pre_processing import PreProcessingPipeline
from tachikoma.projects import ProjectsContextProvider, ProjectsProcessor, projects_hook
from tachikoma.repl import Repl
from tachikoma.scheduler import CronTrigger, IntervalTrigger, Job, scheduler
from tachikoma.session_context import SessionContext
from tachikoma.sessions import session_recovery_hook
from tachikoma.skills import SkillRegistry, SkillsContextProvider, skills_hook, watch_skills
from tachikoma.tasks import (
    TaskRepository,
    create_task_tools_server,
)
from tachikoma.tasks.executor import (
    RUNNER_CHECK_INTERVAL_SECONDS,
    BackgroundTaskRunner,
    expired_waiter_sweep,
)
from tachikoma.tasks.hooks import tasks_hook
from tachikoma.tasks.scheduler import (
    GENERATION_INTERVAL_SECONDS,
    get_timezone,
    instance_generator_tick,
    one_shot_cleanup_tick,
    session_task_scheduler_tick,
)
from tachikoma.telegram import TelegramChannel, telegram_hook
from tachikoma.updates import create_update_tools_server, update_checker_tick, updates_hook
from tachikoma.updates.rollback import (
    clear_restart_notification,
    clear_rollback_marker,
    clear_rollback_notification,
    handle_restart_notification,
    read_restart_notification,
    read_rollback_marker,
    read_rollback_notification,
    run_rollback,
    write_restart_notification,
    write_rollback_notification,
)
from tachikoma.workflows.cleanup import StaleWorkflowCleanupProcessor
from tachikoma.workflows.hooks import workflows_hook
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import create_workflow_tools_server
from tachikoma.workspace import workspace_hook

_log = logger.bind(component="main")

app = App()


async def _plugin_check_tick(manager, state_repo, bus: EventBus) -> None:
    """Daily tick: check git-source plugins for updates and notify."""
    updates = await run_daily_git_check(manager.loaded_plugins(), state_repo)

    if not updates:
        return

    lines = ["Plugin updates available:\n"]
    for info in updates:
        short_sha = info.available_version[:8] if info.available_version else "?"
        lines.append(f"  - {info.alias} ({short_sha})")
    lines.append("\nUse update_plugin(<alias>) or update_all_plugins() to apply updates.")

    await dispatch_notification(
        bus,
        source="Plugin Update Check",
        content="\n".join(lines),
        severity="info",
        source_id="plugin_update_check",
    )


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

    # Check for rollback markers from a previous post-update restart
    rollback_marker = read_rollback_marker()
    rollback_notification = read_rollback_notification()
    restart_notification = read_restart_notification()

    bootstrap = Bootstrap(settings_manager)
    bus = EventBus()
    bootstrap.extras["event_bus"] = bus
    bootstrap.register("workspace", workspace_hook)
    bootstrap.register("logging", logging_hook)
    bootstrap.register("git", git_hook)
    bootstrap.register("database", database_hook)
    bootstrap.register("plugins", plugins_hook)
    bootstrap.register("updates", updates_hook)
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

        if rollback_marker is not None:
            _log.warning(
                "Bootstrap failed after update from {prev} to {target}, rolling back",
                prev=rollback_marker.previous_version,
                target=rollback_marker.target_version,
            )
            print(
                f"Update to {rollback_marker.target_version} failed, "
                f"rolling back to {rollback_marker.previous_version}...",
                file=sys.stderr,
            )
            if run_rollback(rollback_marker.previous_version):
                write_rollback_notification(
                    rollback_marker.previous_version,
                    rollback_marker.target_version,
                    str(e),
                )
                clear_rollback_marker()
                # Drop any stale "back online" marker from the prior successful
                # update so the recovered run doesn't announce with outdated versions.
                clear_restart_notification()
                _log.info(
                    "Rollback succeeded, restarting with {ver}",
                    ver=rollback_marker.previous_version,
                )
                os.execv(sys.argv[0], sys.argv)
            else:
                clear_rollback_marker()
                clear_restart_notification()
                print(
                    f"Rollback to {rollback_marker.previous_version} failed. "
                    "Manual intervention required.",
                    file=sys.stderr,
                )
                _log.error("Rollback failed, exiting for manual intervention")

        sys.exit(1)

    if rollback_marker is not None:
        _log.info(
            "Update confirmed: {prev} -> {target}",
            prev=rollback_marker.previous_version,
            target=rollback_marker.target_version,
        )
        clear_rollback_marker()

    settings = settings_manager.settings

    # Run plugin init hooks after all subsystems are bootstrapped
    # but before the coordinator processes any message.
    from tachikoma.plugins.lifecycle import run_plugin_init_hooks  # noqa: PLC0415

    plugin_manager = bootstrap.extras["plugin_manager"]
    await run_plugin_init_hooks(plugin_manager.list_plugins(), bus)

    # Retrieve the shared database and subsystem objects from bootstrap
    database: Database = bootstrap.extras["database"]
    registry = bootstrap.extras["session_registry"]
    task_repository: TaskRepository = bootstrap.extras["task_repository"]
    skill_registry: SkillRegistry = bootstrap.extras["skill_registry"]
    workflow_repository: WorkflowStateRepository = bootstrap.extras["workflow_repository"]
    process_repository: ProcessRepository = bootstrap.extras["process_repository"]
    detached_log_dir: Path = bootstrap.extras["detached_process_log_dir"]
    app_state_repo = bootstrap.extras["app_state_repository"]
    plugin_state_repo = bootstrap.extras["state_repo"]

    # Dispatch rollback notification if a previous update was rolled back
    if rollback_notification is not None:
        await dispatch_notification(
            bus,
            source="Update Rollback",
            content=(
                f"Update from {rollback_notification.previous_version} to "
                f"{rollback_notification.failed_version} failed and was rolled back "
                f"to {rollback_notification.previous_version}.\n\n"
                f"Error: {rollback_notification.error}"
            ),
            severity="error",
            source_id="update_rollback",
        )
        clear_rollback_notification()

    rollback_was_dispatched = rollback_notification is not None

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
    pipeline.register(ProjectsProcessor(agent_defaults))
    pipeline.register(StaleWorkflowCleanupProcessor(workflow_repository))
    pipeline.register(TranscriptArchiveProcessor(agent_defaults))
    pipeline.register(GitProcessor(agent_defaults))

    # Create and configure the pre-processing pipeline (session-gated: projects)
    pre_pipeline = PreProcessingPipeline()
    pre_pipeline.register(ProjectsContextProvider(workspace_path=settings.workspace.path))

    # Create and configure the per-message pre-processing pipeline (skills, memory)
    msg_pre_pipeline = MessagePreProcessingPipeline()
    msg_pre_pipeline.register(SkillsContextProvider(agent_defaults, skill_registry))
    msg_pre_pipeline.register(MemoryContextProvider(agent_defaults))

    # Wire provider listeners for plugin context provider lifecycle
    from tachikoma.plugins.provider_listeners import register_plugin_provider_listeners  # noqa: I001, PLC0415

    register_plugin_provider_listeners(
        bus, pre_pipeline, msg_pre_pipeline, plugin_manager, pipeline
    )

    # Create and configure the per-message post-processing pipeline
    msg_pipeline = MessagePostProcessingPipeline()
    msg_pipeline.register(SummaryProcessor(registry=registry, agent_defaults=agent_defaults))
    msg_pipeline.register(LastExchangeProcessor(registry=registry))

    task_tools = create_task_tools_server(
        task_repository,
        ZoneInfo(settings.tasks.timezone),
        skill_registry=skill_registry,
    )
    background_task_tools = create_task_tools_server(
        task_repository,
        ZoneInfo(settings.tasks.timezone),
        include_respond_tool=False,
        skill_registry=skill_registry,
    )
    session_context = SessionContext()

    workflow_tools = create_workflow_tools_server(
        workflow_repository,
        skill_registry,
        settings.workspace.path,
        agent_defaults=agent_defaults,
        session_context=session_context,
    )
    detached_process_tools = create_detached_process_tools_server(
        process_repository,
        bus,
        detached_log_dir,
        ZoneInfo(settings.tasks.timezone),
    )
    git_tools = create_git_tools_server(settings.workspace.path, agent_defaults)
    update_tools = create_update_tools_server(bus)
    plugin_tools = create_plugin_tools_server(bootstrap.extras["plugin_manager"])

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
        "update-tools": update_tools,
        "plugin-tools": plugin_tools,
        **channel_mcp,
    }

    scheduler_tasks: list[asyncio.Task[None]] = []
    buffer: Buffer | None = None
    background_runner: BackgroundTaskRunner | None = None
    restart_needed: bool = False

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
            session_context=session_context,
        ) as coordinator:
            buffer = await create_and_start_buffer(
                bus=bus,
                coordinator=coordinator,
                settings=settings.buffer,
            )

            background_runner = BackgroundTaskRunner(
                repository=task_repository,
                settings=settings.tasks,
                bus=bus,
                agent_defaults=agent_defaults,
                skill_registry=skill_registry,
                session_registry=registry,
                extra_mcp_servers={
                    "git-tools": git_tools,
                    "task-tools": background_task_tools,
                    "workflow-tools": workflow_tools,
                },
                hooks=[destructive_git_deny_hook],
            )

            tz = get_timezone(settings.tasks)
            jobs = [
                Job(
                    name="instance_generator",
                    trigger=IntervalTrigger(GENERATION_INTERVAL_SECONDS),
                    run=lambda: instance_generator_tick(task_repository, settings.tasks),
                ),
                Job(
                    name="session_task_scheduler",
                    trigger=IntervalTrigger(settings.tasks.check_interval),
                    run=lambda: session_task_scheduler_tick(
                        task_repository, settings.tasks, buffer
                    ),
                ),
                Job(
                    name="background_task_runner",
                    trigger=IntervalTrigger(RUNNER_CHECK_INTERVAL_SECONDS),
                    run=background_runner.tick,
                ),
                Job(
                    name="expired_waiter_sweep",
                    trigger=IntervalTrigger(120),
                    run=lambda: expired_waiter_sweep(task_repository, settings.tasks, bus),
                ),
                Job(
                    name="one_shot_cleanup",
                    trigger=CronTrigger("0 3 * * *", tz),
                    run=lambda: one_shot_cleanup_tick(task_repository, settings.tasks),
                ),
            ]

            if settings.updates.enabled:
                jobs.append(
                    Job(
                        name="update_checker",
                        trigger=IntervalTrigger(settings.updates.check_interval),
                        run=lambda: update_checker_tick(app_state_repo, bus),
                    )
                )

            jobs.append(
                Job(
                    name="plugin_update_check",
                    trigger=CronTrigger("17 3 * * *", tz),
                    run=lambda: _plugin_check_tick(plugin_manager, plugin_state_repo, bus),
                )
            )

            if settings.memory.maintenance.enabled:
                maintenance_schedule = settings.memory.maintenance.schedule
                maintenance_settings = settings.memory.maintenance
                jobs.extend(
                    [
                        Job(
                            name="episodic_maintenance",
                            trigger=CronTrigger(maintenance_schedule, tz),
                            run=lambda: episodic_maintenance_tick(
                                agent_defaults, maintenance_settings
                            ),
                        ),
                        Job(
                            name="facts_maintenance",
                            trigger=CronTrigger(maintenance_schedule, tz),
                            run=lambda: facts_maintenance_tick(
                                agent_defaults, skill_registry
                            ),
                        ),
                        Job(
                            name="preferences_maintenance",
                            trigger=CronTrigger(maintenance_schedule, tz),
                            run=lambda: preferences_maintenance_tick(
                                agent_defaults, skill_registry
                            ),
                        ),
                        Job(
                            name="context_maintenance",
                            trigger=CronTrigger(maintenance_schedule, tz),
                            run=lambda: context_maintenance_tick(
                                agent_defaults, skill_registry
                            ),
                        ),
                    ]
                )

            scheduler_tasks.append(asyncio.create_task(scheduler(jobs), name="scheduler"))

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

            # Per DES-011: clear marker unconditionally before side effects.
            await handle_restart_notification(
                bus,
                restart_notification,
                rollback_was_dispatched,
                plugin_manager=bootstrap.extras["plugin_manager"],
            )

            # Start channel with coordinator
            await active_channel.run(coordinator)

            # Capture restart flag before Coordinator.__aexit__ runs cleanup
            restart_needed = active_channel.restart_requested

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

        # Drain any in-flight background executor tasks spawned by the runner
        if background_runner is not None:
            try:
                await background_runner.shutdown()
            except Exception:
                _log.exception("Background runner shutdown failed")

        # Stop the event bus
        await bus.stop()

        # Close channel resources after coordinator cleanup (allows shutdown status messages).
        try:
            await active_channel.close()
        except Exception:
            _log.exception("Channel close failed during shutdown")

        # Dispose the shared database engine to prevent dangling connections
        if database is not None:
            await database.close()

    # In-place restart after successful upgrade — all async resources are released.
    # Re-read the rollback marker fresh: presence here means apply_update ran in
    # this session and the boot it triggers has not yet succeeded — i.e., update
    # restart; absence means manual restart.
    if restart_needed:
        current_rollback = read_rollback_marker()
        if current_rollback is not None:
            write_restart_notification(
                reason="update",
                previous_version=current_rollback.previous_version,
                new_version=current_rollback.target_version,
            )
        else:
            write_restart_notification(
                reason="manual",
                previous_version=None,
                new_version=None,
            )
        _log.info("Restarting after update...")
        os.execv(sys.argv[0], sys.argv)


@app.default
async def default_command() -> None:
    """Default command — delegates to run."""
    await run()


if __name__ == "__main__":
    cli()
