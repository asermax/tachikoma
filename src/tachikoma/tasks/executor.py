"""Background task execution for task subsystem.

This module contains:
- BackgroundTaskRunner: tick-driven dispatcher that spawns executor tasks for ready instances
- expired_waiter_sweep: fails waiting instances that have exceeded the wait_timeout
- BackgroundTaskExecutor: executes a single background task with evaluator loop
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bubus import EventBus
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    McpSdkServerConfig,
    ResultMessage,
    SystemPromptPreset,
    TextBlock,
)
from loguru import logger

from tachikoma.adapter import sanitize_text
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.buffer.priority import Priority
from tachikoma.config import TaskSettings
from tachikoma.git.processor import GitProcessor
from tachikoma.memory.context_provider import MemoryContextProvider
from tachikoma.memory.episodic import EpisodicProcessor
from tachikoma.message import TextMessage
from tachikoma.notifications import (
    NotificationCycleState,
    create_notification_server,
    dispatch_notification,
)
from tachikoma.per_message_pre_processing import MessagePreProcessingPipeline
from tachikoma.post_processing import PRE_FINALIZE_PHASE, PostProcessingPipeline
from tachikoma.pre_processing import (
    McpServerConfig,
    PreProcessingPipeline,
    assemble_context,
)
from tachikoma.projects.context_provider import ProjectsContextProvider
from tachikoma.projects.processor import ProjectsProcessor
from tachikoma.sdk_query import StderrAccumulator
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.skills.context_provider import SkillsContextProvider
from tachikoma.tasks.model import TaskDefinition, TaskInstance
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import get_timezone

if TYPE_CHECKING:
    from tachikoma.skills.registry import SkillRegistry
    from tachikoma.workflows.repository import WorkflowStateRepository
    from tachikoma.workflows.step_context import WorkflowStepContextProvider
    from tachikoma.workflows.step_prompt import WORKFLOW_STEP_SYSTEM_PROMPT
    from tachikoma.workflows.step_tools import create_workflow_step_tools_server


@dataclass
class _PreprocessingResult:
    """Result from background task pre-processing.

    Holds the enriched prompt text plus MCP server configurations
    extracted from context providers.
    """

    prompt: str
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)


_log = logger.bind(component="task_executor")

# Cadence at which BackgroundTaskRunner.tick is driven by the central scheduler
RUNNER_CHECK_INTERVAL_SECONDS = 30


async def _resolve_source(
    repository: TaskRepository,
    instance: TaskInstance,
) -> str:
    """Resolve the notification source for an instance."""
    # TODO(Batch 8): workflow step instances should resolve to
    # "Workflow: skill/name" by reading the workflow chain, but that
    # requires threading WorkflowStateRepository through the sweep
    # functions and scheduler job builder. For now workflow step
    # instances use the generic "Background task: ..." format in sweep
    # notifications (the live executor path is already correct).
    definition = (
        await repository.get_definition(instance.definition_id) if instance.definition_id else None
    )
    return (
        f"Background task: {definition.name}"
        if definition
        else f"Background task: {instance.prompt[:100]}"
    )


async def _fail_timed_out_instances(
    instances: list[TaskInstance],
    repository: TaskRepository,
    bus: EventBus,
    *,
    reason: str,
    log_message: str,
) -> None:
    """Fail instances and dispatch notifications for a sweep."""
    for instance in instances:
        source = await _resolve_source(repository, instance)

        await repository.update_instance(
            instance.id,
            status="failed",
            completed_at=datetime.now(UTC),
            result=reason,
        )
        await dispatch_notification(
            bus,
            source,
            f"Task failed: {reason}",
            "error",
            instance.id,
            priority=Priority.URGENT,
        )

    if instances:
        _log.info(log_message, count=len(instances))


async def expired_waiter_sweep(
    repository: TaskRepository,
    settings: TaskSettings,
    bus: EventBus,
) -> None:
    """Fail waiting instances that have exceeded the applicable wait_timeout."""
    # Regular background tasks: standard wait_timeout
    regular_expired = await repository.list_expired_waiting_instances(
        settings.wait_timeout, only_workflow_tasks=False
    )
    await _fail_timed_out_instances(
        regular_expired,
        repository,
        bus,
        reason=f"Task timed out waiting for user input after {settings.wait_timeout}s",
        log_message="Expired {count} regular waiting instances past timeout",
    )

    # Workflow step tasks: longer workflow_wait_timeout
    workflow_expired = await repository.list_expired_waiting_instances(
        settings.workflow_wait_timeout, only_workflow_tasks=True
    )
    await _fail_timed_out_instances(
        workflow_expired,
        repository,
        bus,
        reason=(
            f"Workflow step timed out waiting for user input "
            f"after {settings.workflow_wait_timeout}s"
        ),
        log_message="Expired {count} workflow waiting instances past timeout",
    )


async def stuck_running_sweep(
    repository: TaskRepository,
    settings: TaskSettings,
    bus: EventBus,
) -> None:
    """Fail running instances that have exceeded the running_timeout."""
    stuck = await repository.list_stuck_running_instances(settings.running_timeout)
    await _fail_timed_out_instances(
        stuck,
        repository,
        bus,
        reason=f"Task exceeded running timeout of {settings.running_timeout}s",
        log_message="Failed {count} stuck running instances past timeout",
    )


# Background task system prompt
BACKGROUND_TASK_SYSTEM_PROMPT = """You are a background task agent. You are executing a scheduled task autonomously. Complete the task described below. Your work will be saved automatically.

You are operating without direct user interaction. Work through the task methodically, and when you believe the task is complete, provide a clear summary of what was accomplished.

## Notifications

You have access to the `send_notification` tool, which delivers a message to the user. Use it when:
- You have meaningful results or findings to share (not for every task)
- You want to provide a progress update during a long-running task
- The task outcome is worth the user's attention

You can call `send_notification` multiple times during execution (e.g., for progress updates). Failure notifications are sent automatically — you do not need to notify on failure.

### Priority Levels

The `send_notification` tool accepts a `priority` parameter with three levels:
- **urgent**: Time-sensitive results the user must see promptly (e.g., critical failures, time-bound alerts)
- **normal**: Standard completion results — the default. Use for most task outcomes and progress updates
- **low**: Informational updates that can wait for a natural break (e.g., routine status checks, non-urgent summaries)

If unsure, use the default (normal).

## Task Scheduling

You have access to the task management tools — `create_task`, `list_tasks`, `get_task`, `update_task`, `delete_task`, and `run_task_now` — to schedule follow-up work during autonomous execution. Use them when:
- You discover work that belongs in a separate scheduled run (e.g., a recurring check, a delayed reminder, or a follow-up pass once an external condition changes)
- You want to split a long investigation into a follow-up task rather than pushing the current run past its scope
- You need to inspect or clean up existing schedules before adding new ones (prefer `list_tasks` / `get_task` before creating to avoid duplicates)
- You want to fire an existing background definition or an ad-hoc prompt immediately via `run_task_now` (e.g., re-running a sibling task or spawning a one-off sub-task)

Newly scheduled tasks produce fresh isolated runs when their schedule fires — they do not nest inside the current execution. Prefer completing the current task's stated goal first, and use scheduling for genuinely separate work rather than as a workaround for the iteration limit.

## Workflows

You have access to the workflow management tools — `start_workflow`, `get_workflow_state`, and `list_active_workflows` — to start and monitor multi-step workflows during autonomous runs. Use them when:
- The task requires a structured multi-step process tracked reliably across context boundaries
- You need to ensure ordered execution of steps without skipping or losing progress
- You want step-specific instructions and skill content loaded automatically as you advance

Once started, workflows execute autonomously as background tasks. Use `list_active_workflows` to check for already-running workflows, and `get_workflow_state` to monitor progress.

## Requesting user input

If you need user input to proceed, use `send_notification` with `await_response` set to `true`. This is the **only** way to request input — your messages are not delivered directly to the user.

Example:
```
send_notification({
  message: "Which database should I run the migration against — staging or production?",
  await_response: true
})
```

When `await_response` is true:
- Your message is delivered to the user as an urgent notification
- Execution pauses until the user replies
- The user's response arrives as the next conversation turn
- Priority is forced to urgent regardless of what you pass

You can request input multiple times during a task if needed. Use this when you have a genuine blocking question — do not use it for questions you can answer from available context."""  # noqa: E501

# Evaluator prompt for assessing task completion
EVALUATOR_PROMPT_TEMPLATE = """You are a task completion evaluator for a background task agent. Your ONLY job is to classify the agent's current workflow state using the ordered rules below.

You are a CLASSIFIER, not a reviewer. You MUST NOT:
- Perform any qualitative analysis of the agent's output (correctness, thoroughness, style, usefulness, depth of investigation)
- Offer corrective feedback, suggestions, improvements, or opinions about what the agent should have done differently
- Apply any criteria beyond the rules listed below

The `rationale` field explains why you chose this classification and must describe observable facts about what the agent said or did — never advice, critique, or evaluation directed at the agent.

**Task Definition:**
{task_prompt}

**Agent's Latest Response:**
{agent_response}

**Classification rules (evaluate in order, use the first match):**

1. **Blocking error**: Did the agent report an unrecoverable error, get stuck in a loop, or fail repeatedly without making progress?
   → {{"status": "stuck", "rationale": "Factual description of the blocking signal the agent reported"}}

2. **Workflow complete**: Did the agent execute the requested actions and announce completion, summarize results, or produce final output? If the agent called `send_notification`, that is a strong signal of completion. Classify as complete even if the agent mentions optional follow-up actions it could take — completion announcements take precedence over hypothetical next steps.
   → {{"status": "complete", "rationale": "Brief factual summary of what the agent reported accomplishing"}}

3. **Mid-workflow**: Is the agent still working — it announced next steps but hasn't executed them yet, or it's partway through a multi-step process?
   → {{"status": "continue", "rationale": "What the agent said it would do next but has not yet executed"}}

Respond with ONLY a JSON object (no other text, no markdown formatting)."""  # noqa: E501


class BackgroundTaskRunner:
    """Tick-driven dispatcher for background task instances.

    State (semaphore, currently running executor tasks) lives on the
    runner instance and persists across ticks. Each ``tick()`` call is
    one pass: query ready instances, spawn executors under the
    semaphore, prune completed tasks.

    Call ``shutdown()`` during application shutdown (after the central
    scheduler has been cancelled) to cancel any executor tasks still
    running and await their completion.
    """

    def __init__(
        self,
        repository: TaskRepository,
        settings: TaskSettings,
        bus: EventBus,
        agent_defaults: AgentDefaults,
        skill_registry: "SkillRegistry",
        session_registry: SessionRegistry,
        workflow_repository: "WorkflowStateRepository | None" = None,
        extra_mcp_servers: dict[str, McpSdkServerConfig] | None = None,
        hooks: list[HookMatcher] | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._bus = bus
        self._agent_defaults = agent_defaults
        self._skill_registry = skill_registry
        self._session_registry = session_registry
        self._workflow_repository = workflow_repository
        self._extra_mcp_servers = extra_mcp_servers
        self._hooks = hooks

        self._semaphore = asyncio.Semaphore(settings.max_concurrent_background)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}

        _log.info(
            "Background task runner initialized (max_concurrent={max})",
            max=settings.max_concurrent_background,
        )

    async def tick(self) -> None:
        """Query ready instances, spawn executors, prune completed tasks."""
        ready_instances = await self._repository.get_ready_background_instances()

        for instance in ready_instances:
            if instance.id in self._running_tasks:
                continue

            task = asyncio.create_task(
                self._run_with_semaphore(instance),
                name=f"bg-exec:{instance.id}",
            )
            self._running_tasks[instance.id] = task

            _log.info(
                "Started execution of background instance {inst_id}",
                inst_id=instance.id,
            )

        completed = [inst_id for inst_id, task in self._running_tasks.items() if task.done()]
        for inst_id in completed:
            task = self._running_tasks.pop(inst_id)
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                _log.exception(
                    "Background task {inst_id} failed: {err}",
                    inst_id=inst_id,
                    err=str(exc),
                )

    async def shutdown(self) -> None:
        """Cancel in-flight executor tasks and await their completion."""
        if not self._running_tasks:
            return

        _log.info(
            "Cancelling {count} running background executors",
            count=len(self._running_tasks),
        )

        for task in self._running_tasks.values():
            task.cancel()

        await asyncio.gather(*self._running_tasks.values(), return_exceptions=True)
        self._running_tasks.clear()

    async def _run_with_semaphore(self, instance: TaskInstance) -> None:
        async with self._semaphore:
            executor = BackgroundTaskExecutor(
                repository=self._repository,
                settings=self._settings,
                bus=self._bus,
                agent_defaults=self._agent_defaults,
                skill_registry=self._skill_registry,
                session_registry=self._session_registry,
                workflow_repository=self._workflow_repository,
                extra_mcp_servers=self._extra_mcp_servers,
                hooks=self._hooks,
            )
            await executor.execute(instance)


class BackgroundTaskExecutor:
    """Executes a single background task in an isolated SDK session.

    Manages ClaudeSDKClient lifecycle with resume for multi-turn conversation.
    Uses an evaluator prompt (separate lightweight query) to assess completion.
    Runs adapted pre-processing (memory context) and post-processing (episodic + git).
    """

    def __init__(
        self,
        repository: TaskRepository,
        settings: TaskSettings,
        bus: EventBus,
        agent_defaults: AgentDefaults,
        skill_registry: "SkillRegistry",
        session_registry: SessionRegistry,
        workflow_repository: "WorkflowStateRepository | None" = None,
        extra_mcp_servers: dict[str, McpSdkServerConfig] | None = None,
        hooks: list[HookMatcher] | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._bus = bus
        self._agent_defaults = agent_defaults
        self._cwd = agent_defaults.cwd
        self._skill_registry = skill_registry
        self._session_registry = session_registry
        self._workflow_repository = workflow_repository
        self._extra_mcp_servers = extra_mcp_servers or {}
        self._hooks = hooks or []

    async def execute(self, instance: TaskInstance) -> None:
        """Execute a background task instance (fresh or resuming a waiter)."""
        resuming = instance.status == "waiting"

        if resuming and instance.sdk_session_id is None:
            await self._fail_instance(instance.id, "Cannot resume — missing sdk_session_id")
            await dispatch_notification(
                self._bus,
                f"Background task: {instance.prompt[:100]}",
                "Task failed: no session to resume from",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )
            return

        if resuming:
            initial_query = instance.user_response or ""
            await self._repository.update_instance(
                instance.id,
                status="running",
                user_response=None,
                started_at=datetime.now(UTC),
            )
            _log.info(
                "Resuming background task instance {inst_id} with sdk_session={sdk_id}",
                inst_id=instance.id,
                sdk_id=instance.sdk_session_id,
            )
        else:
            await self._repository.update_instance(
                instance.id,
                status="running",
                started_at=datetime.now(UTC),
            )
            _log.info(
                "Executing background task instance {inst_id}",
                inst_id=instance.id,
            )

        stderr_acc = StderrAccumulator()
        notification_source = f"Background task: {instance.prompt[:100]}"
        is_workflow_step = instance.workflow_id is not None

        try:
            definition: TaskDefinition | None = None
            if instance.definition_id:
                definition = await self._repository.get_definition(instance.definition_id)

            notification_source = (
                f"Background task: {definition.name}"
                if definition
                else f"Background task: {instance.prompt[:100]}"
            )

            preprocessing_result = await self._run_preprocessing(
                instance.prompt,
                pinned_skills=definition.skills if definition else (),
                instance=instance,
            )

            notif_state = NotificationCycleState()

            # Workflow step tasks: compute workflow-specific notification source
            # and register step tools BEFORE creating the notification server,
            # so the notification server uses the correct source label.
            if is_workflow_step and self._workflow_repository is not None:
                notification_source = await self._register_workflow_step_tools(
                    preprocessing_result, instance, notif_state, notification_source
                )

            preprocessing_result.mcp_servers["notifications"] = create_notification_server(
                self._bus,
                notification_source,
                instance.id,
                cycle_state=notif_state,
            )

            # Merge any always-on extra servers (e.g. git-tools) without
            # letting them shadow per-invocation servers. Workflow step tasks
            # exclude workflow-tools to prevent recursive workflow management.
            for name, server in self._extra_mcp_servers.items():
                if is_workflow_step and name == "workflow-tools":
                    continue
                preprocessing_result.mcp_servers.setdefault(name, server)

            tz = get_timezone(self._settings)
            now = datetime.now(tz)
            datetime_line = (
                f"Current date and time: {now.strftime('%A, %B %d, %Y at %H:%M:%S')} {tz.key}\n"
            )

            if is_workflow_step:
                system_prompt_text = datetime_line + "\n" + WORKFLOW_STEP_SYSTEM_PROMPT
            else:
                system_prompt_text = datetime_line + "\n" + BACKGROUND_TASK_SYSTEM_PROMPT

            options = ClaudeAgentOptions(
                cwd=self._agent_defaults.cwd,
                cli_path=self._agent_defaults.cli_path,
                env=self._agent_defaults.env,
                disallowed_tools=list(self._agent_defaults.disallowed_tools),
                system_prompt=SystemPromptPreset(
                    type="preset",
                    preset="claude_code",
                    append=system_prompt_text,
                ),
                permission_mode="bypassPermissions",
                mcp_servers=preprocessing_result.mcp_servers,
                stderr=stderr_acc,
                resume=instance.sdk_session_id if resuming else None,
            )

            if self._hooks:
                options.hooks = {"PreToolUse": self._hooks}

            first_message = initial_query if resuming else preprocessing_result.prompt
            # On resume, seed the evaluator with the known session id so the
            # await_response branch has a valid resume target even if the run
            # errors before a ResultMessage is observed.
            initial_sdk_session_id = instance.sdk_session_id if resuming else None

            async with ClaudeSDKClient(options) as client:
                await client.query(first_message)

                await self._run_evaluator_loop(
                    client,
                    instance,
                    notification_source,
                    stderr_acc,
                    initial_sdk_session_id,
                    notif_state,
                )

        except asyncio.CancelledError:
            _log.info("Background task {inst_id} cancelled", inst_id=instance.id)
            await self._fail_instance(instance.id, "Task cancelled")
            await self._run_postprocessing(instance.sdk_session_id, instance, is_failure=True)
            raise

        except Exception as exc:
            stderr = stderr_acc.get()
            if stderr is not None:
                _log.exception(
                    "Background task {inst_id} failed with error: {err}, stderr={stderr}",
                    inst_id=instance.id,
                    err=str(exc),
                    stderr=stderr,
                )
            else:
                _log.exception(
                    "Background task {inst_id} failed with error: {err}",
                    inst_id=instance.id,
                    err=str(exc),
                )
            await self._fail_instance(instance.id, str(exc))
            await dispatch_notification(
                self._bus,
                notification_source,
                f"Task failed with error: {exc}",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )
            await self._run_postprocessing(instance.sdk_session_id, instance, is_failure=True)

    async def _run_evaluator_loop(
        self,
        client: ClaudeSDKClient,
        instance: TaskInstance,
        notification_source: str,
        stderr_acc: StderrAccumulator,
        initial_sdk_session_id: str | None,
        notif_state: NotificationCycleState,
    ) -> None:
        """Run the evaluator loop until completion or failure."""
        sdk_session_id = initial_sdk_session_id
        response_text = ""
        iteration = 0
        max_iterations = self._settings.max_iterations

        while iteration < max_iterations:
            iteration += 1
            notif_state.reset()

            # The notification handler may set await_response_requested during tool
            # execution inside this stream — check it after, before running evaluator.
            response_chunks: list[str] = []
            async for sdk_message in client.receive_response():
                if isinstance(sdk_message, ResultMessage) and sdk_message.session_id:
                    sdk_session_id = sdk_message.session_id

                if isinstance(sdk_message, AssistantMessage):
                    for block in sdk_message.content:
                        if isinstance(block, TextBlock):
                            response_chunks.append(sanitize_text(block.text))

            response_text = "".join(response_chunks)

            # Agent explicitly requested user input via await_response
            if notif_state.await_response_requested:
                if sdk_session_id is None:
                    await self._fail_instance(
                        instance.id,
                        "Cannot pause — no SDK session ID captured yet",
                    )
                    await dispatch_notification(
                        self._bus,
                        notification_source,
                        "Task failed: no session to resume from",
                        "error",
                        instance.id,
                        priority=Priority.URGENT,
                    )
                    return

                _log.info(
                    "await_response requested for {inst_id}, transitioning to waiting",
                    inst_id=instance.id,
                )
                await self._repository.update_instance(
                    instance.id,
                    status="waiting",
                    sdk_session_id=sdk_session_id,
                )
                return

            # Evaluator runs only when agent didn't explicitly request input
            eval_result = await self._run_evaluator(
                instance.prompt,
                response_text,
            )

            status = eval_result.get("status", "continue")
            rationale = eval_result.get("rationale", "")

            _log.debug(
                "Evaluator result for {inst_id}: status={status}",
                inst_id=instance.id,
                status=status,
            )

            if status == "complete":
                await self._complete_instance(instance.id, rationale)
                await self._run_postprocessing(sdk_session_id, instance)
                return

            if status == "stuck":
                await self._fail_instance(instance.id, f"Agent stuck: {rationale}")
                await dispatch_notification(
                    self._bus,
                    notification_source,
                    f"Task failed: {rationale}",
                    "error",
                    instance.id,
                    priority=Priority.URGENT,
                )
                await self._run_postprocessing(sdk_session_id, instance, is_failure=True)
                return

            # Continue: inject the evaluator's factual rationale
            await client.query(rationale)

        # Max iterations reached
        _log.warning(
            "Background task {inst_id} reached max iterations",
            inst_id=instance.id,
        )
        await self._fail_instance(
            instance.id,
            f"Max iterations ({max_iterations}) reached without completion",
        )
        await dispatch_notification(
            self._bus,
            notification_source,
            f"Task failed: reached max iterations ({max_iterations})",
            "error",
            instance.id,
            priority=Priority.URGENT,
        )
        await self._run_postprocessing(sdk_session_id, instance, is_failure=True)

    async def _register_workflow_step_tools(
        self,
        preprocessing_result: _PreprocessingResult,
        instance: TaskInstance,
        notif_state: NotificationCycleState,
        notification_source: str,
    ) -> str:
        """Register workflow step MCP tools for a workflow step task.

        Returns the workflow-specific notification source so the caller can
        use it for the notification server and evaluator loop.
        """
        assert self._workflow_repository is not None  # guarded by caller
        assert instance.workflow_id is not None  # guarded by is_workflow_step

        # Build workflow-specific notification source (R6: "Workflow: skill/name")
        chain = await self._workflow_repository.get_active_chain(instance.workflow_id)
        if chain:
            top = chain[0]
            notification_source = f"Workflow: {top.skill_name}/{top.workflow_name}"

        step_server = create_workflow_step_tools_server(
            repository=self._workflow_repository,
            task_repository=self._repository,
            skill_registry=self._skill_registry,
            bus=self._bus,
            workflow_id=instance.workflow_id,
            instance_id=instance.id,
            cycle_state=notif_state,
            notification_source=notification_source,
        )
        preprocessing_result.mcp_servers["workflow-step-tools"] = step_server
        return notification_source

    async def _run_preprocessing(
        self,
        prompt: str,
        *,
        pinned_skills: tuple[str, ...] = (),
        instance: TaskInstance | None = None,
    ) -> _PreprocessingResult:
        """Run pre-processing pipeline for context injection.

        Registers context providers (memory, projects, skills) and
        extracts MCP servers from results alongside the enriched prompt text.
        For workflow step tasks, also registers WorkflowStepContextProvider.

        Args:
            prompt: The original task prompt
            pinned_skills: Skill names to load unconditionally before classification.
            instance: The TaskInstance being executed (for workflow step detection).

        Returns:
            PreprocessingResult with enriched prompt and MCP servers.
        """
        try:
            pipeline = PreProcessingPipeline()
            pipeline.register(ProjectsContextProvider(workspace_path=self._cwd))

            # Workflow step tasks get a dedicated context provider
            if (
                instance is not None
                and instance.workflow_id is not None
                and self._workflow_repository is not None
            ):
                pipeline.register(
                    WorkflowStepContextProvider(
                        instance=instance,
                        repository=self._workflow_repository,
                        skill_registry=self._skill_registry,
                    )
                )

            results = await pipeline.run(prompt)

            msg_pipeline = MessagePreProcessingPipeline()
            msg_pipeline.register(MemoryContextProvider(self._agent_defaults))

            if self._skill_registry is not None:
                msg_pipeline.register(
                    SkillsContextProvider(self._agent_defaults, self._skill_registry)
                )

            per_message_results = await msg_pipeline.run(
                TextMessage(text=prompt, pinned_skills=pinned_skills)
            )

            all_results = (results or []) + per_message_results

            if not all_results:
                return _PreprocessingResult(prompt=prompt)

            merged_servers: dict[str, McpServerConfig] = {}

            for r in all_results:
                if r.mcp_servers:
                    merged_servers.update(r.mcp_servers)

            enriched_prompt = assemble_context(all_results, prompt)

            return _PreprocessingResult(
                prompt=enriched_prompt,
                mcp_servers=merged_servers,
            )

        except Exception as exc:
            _log.warning(
                "Pre-processing failed, using original prompt: {err}",
                err=str(exc),
            )

        return _PreprocessingResult(prompt=prompt)

    async def _run_evaluator(
        self,
        task_prompt: str,
        agent_response: str,
    ) -> dict[str, Any]:
        """Run evaluator to assess task completion.

        Args:
            task_prompt: The original task prompt
            agent_response: The agent's latest response

        Returns:
            Parsed evaluator result with status and rationale
        """
        from tachikoma.sdk_query import stderr_aware_query  # noqa: PLC0415

        eval_prompt = EVALUATOR_PROMPT_TEMPLATE.format(
            task_prompt=task_prompt,
            agent_response=agent_response[:4000],  # Truncate to avoid token limits
        )

        options = ClaudeAgentOptions(
            model=self._agent_defaults.classifier_model,
            tools=[],
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
        )

        response_text = ""
        try:
            # DES-005: Fully consume the generator
            async for message in stderr_aware_query(prompt=eval_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_text += sanitize_text(block.text)
        except Exception as exc:
            _log.warning("Evaluator query failed: {err}", err=str(exc))
            return {"status": "continue", "rationale": "Evaluator failed, continuing"}

        # Parse JSON response
        try:
            # Extract JSON from response (handle potential markdown wrapping)
            json_str = response_text.strip()
            if json_str.startswith("```"):
                # Remove markdown code block
                lines = json_str.split("\n")
                json_str = "\n".join(lines[1:-1])

            return json.loads(json_str)
        except json.JSONDecodeError:
            _log.warning(
                "Failed to parse evaluator response as JSON: {response}",
                response=response_text[:200],
            )
            return {"status": "continue", "rationale": "Could not parse evaluator response"}

    async def _run_postprocessing(
        self,
        sdk_session_id: str | None,
        instance: TaskInstance | None = None,
        *,
        is_failure: bool = False,
    ) -> None:
        """Run adapted post-processing pipeline (episodic + git only).

        Args:
            sdk_session_id: The SDK session ID from the background task execution
            instance: Optional TaskInstance — when provided and has a workflow_id,
                the WorkflowFailureProcessor is registered to handle abort cascade
                on failed workflow step tasks.
            is_failure: When True and instance has a workflow_id, registers
                the WorkflowFailureProcessor for abort cascade. Must be False
                on success paths to avoid aborting healthy workflows.
        """
        needs_failure_processor = (
            is_failure
            and instance is not None
            and instance.workflow_id is not None
            and self._workflow_repository is not None
        )

        # When there's no SDK session but a failure processor is needed,
        # run a minimal pipeline with just the failure processor.
        if sdk_session_id is None:
            if not needs_failure_processor:
                _log.warning("No SDK session ID, skipping post-processing")
                return
            try:
                session = Session(
                    id="background-task",
                    sdk_session_id="no-session",
                    started_at=(now := datetime.now(UTC)),
                    ended_at=now,
                    summary=None,
                    transcript_path=None,
                )
                from tachikoma.workflows.failure_processor import (  # noqa: PLC0415
                    WorkflowFailureProcessor,
                )

                pipeline = PostProcessingPipeline(self._session_registry)
                pipeline.register(
                    WorkflowFailureProcessor(
                        instance=instance,  # type: ignore[arg-type]
                        repository=self._workflow_repository,  # type: ignore[arg-type]
                        bus=self._bus,
                    ),
                    phase="main",
                )
                await pipeline.run(session)
            except Exception as exc:
                _log.exception(
                    "Post-processing failed for background task: {err}",
                    err=str(exc),
                )
            return

        try:
            # Build a minimal Session for the pipeline
            session = Session(
                id="background-task",  # Synthetic ID for background tasks
                sdk_session_id=sdk_session_id,
                started_at=(now := datetime.now(UTC)),
                ended_at=now,
                summary=None,
                transcript_path=None,
            )

            pipeline = PostProcessingPipeline(self._session_registry)
            pipeline.register(
                EpisodicProcessor(self._agent_defaults),
                phase="main",
            )

            # Workflow failure processor — abort cascade on failed workflow tasks
            if needs_failure_processor:
                from tachikoma.workflows.failure_processor import (  # noqa: PLC0415
                    WorkflowFailureProcessor,
                )

                pipeline.register(
                    WorkflowFailureProcessor(
                        instance=instance,  # type: ignore[arg-type]
                        repository=self._workflow_repository,  # type: ignore[arg-type]
                        bus=self._bus,
                    ),
                    phase="main",
                )

            pipeline.register(
                ProjectsProcessor(self._agent_defaults),
                phase=PRE_FINALIZE_PHASE,
            )
            pipeline.register(
                GitProcessor(self._agent_defaults),
                phase="finalize",
            )

            await pipeline.run(session)

        except Exception as exc:
            _log.exception(
                "Post-processing failed for background task: {err}",
                err=str(exc),
            )

    async def _complete_instance(self, instance_id: str, result: str) -> None:
        """Mark instance as completed."""
        await self._repository.update_instance(
            instance_id,
            status="completed",
            completed_at=datetime.now(UTC),
            result=result,
        )
        _log.info("Background task {inst_id} completed", inst_id=instance_id)

    async def _fail_instance(self, instance_id: str, reason: str) -> None:
        """Mark instance as failed."""
        await self._repository.update_instance(
            instance_id,
            status="failed",
            completed_at=datetime.now(UTC),
            result=reason,
        )
        _log.warning(
            "Background task {inst_id} failed: {reason}",
            inst_id=instance_id,
            reason=reason,
        )
