"""Background task execution for task subsystem.

This module contains:
- background_task_runner: async loop that picks up and executes pending background tasks
- BackgroundTaskExecutor: executes a single background task with evaluator loop
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bubus import EventBus
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemPromptPreset, TextBlock
from loguru import logger

from tachikoma.adapter import sanitize_text
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.config import TaskSettings
from tachikoma.git.processor import GitProcessor
from tachikoma.memory.context_provider import MemoryContextProvider
from tachikoma.memory.episodic import EpisodicProcessor
from tachikoma.notifications import (
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
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.skills.context_provider import SkillsContextProvider
from tachikoma.tasks.model import TaskDefinition, TaskInstance
from tachikoma.tasks.repository import TaskRepository
from tachikoma.tasks.scheduler import get_timezone

if TYPE_CHECKING:
    from tachikoma.skills.registry import SkillRegistry


@dataclass
class _PreprocessingResult:
    """Result from background task pre-processing.

    Holds the enriched prompt text plus MCP server configurations
    extracted from context providers.
    """

    prompt: str
    mcp_servers: dict[str, McpServerConfig] = field(default_factory=dict)


_log = logger.bind(component="task_executor")

# How often the background task runner checks for pending instances
RUNNER_CHECK_INTERVAL_SECONDS = 30

# Background task system prompt
BACKGROUND_TASK_SYSTEM_PROMPT = """You are a background task agent. You are executing a scheduled task autonomously. Complete the task described below. Your work will be saved automatically.

You are operating without direct user interaction. Work through the task methodically, and when you believe the task is complete, provide a clear summary of what was accomplished.

## Notifications

You have access to the `send_notification` tool, which delivers a message to the user. Use it when:
- You have meaningful results or findings to share (not for every task)
- You want to provide a progress update during a long-running task
- The task outcome is worth the user's attention

You can call `send_notification` multiple times during execution (e.g., for progress updates). Failure notifications are sent automatically — you do not need to notify on failure."""  # noqa: E501

# Evaluator prompt for assessing task completion
EVALUATOR_PROMPT_TEMPLATE = """You are a task completion evaluator. Assess whether the following background task has been completed.

**Task Definition:**
{task_prompt}

**Agent's Latest Response:**
{agent_response}

**Instructions:**
1. Read the task definition and the agent's response
2. Determine if the task is complete, needs more work, or the agent is stuck
3. Respond with ONLY a JSON object (no other text):

If the task is complete:
{{"status": "complete", "feedback": "Brief summary of what was accomplished"}}

If the agent should continue working:
{{"status": "continue", "feedback": "Specific guidance for what to do next"}}

If the agent is stuck or looping:
{{"status": "stuck", "feedback": "Description of why the agent appears stuck"}}

Respond with ONLY the JSON object, no markdown formatting."""  # noqa: E501


async def background_task_runner(
    repository: TaskRepository,
    settings: TaskSettings,
    bus: EventBus,
    agent_defaults: AgentDefaults,
    skill_registry: "SkillRegistry",
    session_registry: SessionRegistry,
) -> None:
    """Async loop that picks up and executes pending background tasks.

    Gated by asyncio.Semaphore for concurrency limiting.
    Spawns BackgroundTaskExecutor for each instance.

    Args:
        repository: TaskRepository for persistence
        settings: TaskSettings with max_concurrent_background and other config
        bus: EventBus for dispatching Notification events
        agent_defaults: Common SDK options (cwd, cli_path, env)
        skill_registry: Shared skill registry for SkillsContextProvider
        session_registry: SessionRegistry for post-processing pipeline
    """
    semaphore = asyncio.Semaphore(settings.max_concurrent_background)
    running_tasks: dict[str, asyncio.Task[None]] = {}

    _log.info(
        "Background task runner started (max_concurrent={max})",
        max=settings.max_concurrent_background,
    )

    while True:
        try:
            # Query pending background instances
            pending_instances = await repository.get_pending_instances("background")

            for instance in pending_instances:
                # Skip if already running
                if instance.id in running_tasks:
                    continue

                # Check if we can acquire semaphore (non-blocking check)
                if semaphore.locked() and len(running_tasks) >= settings.max_concurrent_background:
                    _log.debug(
                        "Max concurrent tasks reached, skipping instance {inst_id}",
                        inst_id=instance.id,
                    )
                    continue

                # Create executor task
                async def run_with_semaphore(inst: TaskInstance) -> None:
                    async with semaphore:
                        executor = BackgroundTaskExecutor(
                            repository=repository,
                            settings=settings,
                            bus=bus,
                            agent_defaults=agent_defaults,
                            skill_registry=skill_registry,
                            session_registry=session_registry,
                        )
                        await executor.execute(inst)

                task = asyncio.create_task(run_with_semaphore(instance))
                running_tasks[instance.id] = task

                _log.info(
                    "Started execution of background instance {inst_id}",
                    inst_id=instance.id,
                )

            # Prune completed tasks
            completed = [inst_id for inst_id, task in running_tasks.items() if task.done()]
            for inst_id in completed:
                task = running_tasks.pop(inst_id)
                # Check for exceptions
                try:
                    task.result()
                except Exception as exc:
                    _log.exception(
                        "Background task {inst_id} failed: {err}",
                        inst_id=inst_id,
                        err=str(exc),
                    )

        except asyncio.CancelledError:
            _log.info("Background task runner cancelled")
            # Cancel all running tasks
            for task in running_tasks.values():
                task.cancel()
            # Wait for all to complete
            if running_tasks:
                await asyncio.gather(*running_tasks.values(), return_exceptions=True)
            raise

        except Exception as exc:
            _log.exception(
                "Background task runner loop error: {err}",
                err=str(exc),
            )

        # Sleep until next check
        try:
            await asyncio.sleep(RUNNER_CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            _log.info("Background task runner stopped")
            raise


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
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._bus = bus
        self._agent_defaults = agent_defaults
        self._cwd = agent_defaults.cwd
        self._skill_registry = skill_registry
        self._session_registry = session_registry

    async def execute(self, instance: TaskInstance) -> None:
        """Execute a background task instance.

        Args:
            instance: The TaskInstance to execute
        """
        now_utc = datetime.now(UTC)

        # Mark instance as running
        await self._repository.update_instance(
            instance.id,
            status="running",
            started_at=now_utc,
        )

        _log.info(
            "Executing background task instance {inst_id}",
            inst_id=instance.id,
        )

        try:
            # Get the definition if available (for notification source naming)
            definition: TaskDefinition | None = None
            if instance.definition_id:
                definition = await self._repository.get_definition(instance.definition_id)

            notification_source = (
                f"Background task: {definition.name}"
                if definition
                else f"Background task: {instance.prompt[:100]}"
            )

            # Run pre-processing pipeline (memory, projects, skills context)
            preprocessing_result = await self._run_preprocessing(instance.prompt)

            notification_server = create_notification_server(
                self._bus,
                notification_source,
                instance.id,
            )
            preprocessing_result.mcp_servers["notifications"] = notification_server

            tz = get_timezone(self._settings)
            now = datetime.now(tz)
            datetime_line = (
                f"Current date and time: {now.strftime('%A, %B %d, %Y at %H:%M:%S')} {tz.key}\n"
            )
            system_prompt_text = datetime_line + "\n" + BACKGROUND_TASK_SYSTEM_PROMPT

            # Build SDK options with adapted system prompt
            options = ClaudeAgentOptions(
                cwd=self._agent_defaults.cwd,
                cli_path=self._agent_defaults.cli_path,
                env=self._agent_defaults.env,
                system_prompt=SystemPromptPreset(
                    type="preset",
                    preset="claude_code",
                    append=system_prompt_text,
                ),
                permission_mode="bypassPermissions",
                mcp_servers=preprocessing_result.mcp_servers,
            )

            # Execute with evaluator loop
            sdk_session_id: str | None = None
            response_text = ""
            iteration = 0
            max_iterations = self._settings.max_iterations

            async with ClaudeSDKClient(options) as client:
                # Initial query
                await client.query(preprocessing_result.prompt)

                while iteration < max_iterations:
                    iteration += 1

                    # Collect response
                    response_chunks: list[str] = []
                    async for sdk_message in client.receive_response():
                        # Extract session ID from result message
                        if isinstance(sdk_message, ResultMessage) and sdk_message.session_id:
                            sdk_session_id = sdk_message.session_id

                        # Collect text content
                        if isinstance(sdk_message, AssistantMessage):
                            for block in sdk_message.content:
                                if isinstance(block, TextBlock):
                                    response_chunks.append(sanitize_text(block.text))

                    response_text = "".join(response_chunks)

                    # Run evaluator
                    eval_result = await self._run_evaluator(
                        instance.prompt,
                        response_text,
                    )

                    status = eval_result.get("status", "continue")
                    feedback = eval_result.get("feedback", "")

                    _log.debug(
                        "Evaluator result for {inst_id}: status={status}",
                        inst_id=instance.id,
                        status=status,
                    )

                    if status == "complete":
                        # Agent controls notifications via send_notification tool
                        await self._complete_instance(instance.id, feedback)
                        await self._run_postprocessing(sdk_session_id)
                        return

                    if status == "stuck":
                        # Agent is stuck
                        await self._fail_instance(instance.id, f"Agent stuck: {feedback}")
                        await dispatch_notification(
                            self._bus,
                            notification_source,
                            f"Task failed: {feedback}",
                            "error",
                            instance.id,
                        )
                        return

                    # Continue: inject feedback as next turn
                    await client.query(feedback)

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
                )

        except asyncio.CancelledError:
            _log.info("Background task {inst_id} cancelled", inst_id=instance.id)
            await self._fail_instance(instance.id, "Task cancelled")
            raise

        except Exception as exc:
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
            )

    async def _run_preprocessing(self, prompt: str) -> _PreprocessingResult:
        """Run pre-processing pipeline for context injection.

        Registers context providers (memory, projects, skills) and
        extracts MCP servers from results alongside the enriched prompt text.

        Args:
            prompt: The original task prompt

        Returns:
            PreprocessingResult with enriched prompt and MCP servers.
        """
        try:
            pipeline = PreProcessingPipeline()
            pipeline.register(ProjectsContextProvider(workspace_path=self._cwd))

            results = await pipeline.run(prompt)

            msg_pipeline = MessagePreProcessingPipeline()
            msg_pipeline.register(MemoryContextProvider(self._agent_defaults))

            if self._skill_registry is not None:
                msg_pipeline.register(
                    SkillsContextProvider(self._agent_defaults, self._skill_registry)
                )

            per_message_results = await msg_pipeline.run(prompt)

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
            Parsed evaluator result with status and feedback
        """
        from claude_agent_sdk import query  # noqa: PLC0415  – lazy for test mockability

        eval_prompt = EVALUATOR_PROMPT_TEMPLATE.format(
            task_prompt=task_prompt,
            agent_response=agent_response[:4000],  # Truncate to avoid token limits
        )

        options = ClaudeAgentOptions(
            model="haiku",  # Lightweight model for evaluation
            tools=[],
            cwd=self._agent_defaults.cwd,
            cli_path=self._agent_defaults.cli_path,
            env=self._agent_defaults.env,
        )

        response_text = ""
        try:
            # DES-005: Fully consume the generator
            async for message in query(prompt=eval_prompt, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            response_text += sanitize_text(block.text)
        except Exception as exc:
            _log.warning("Evaluator query failed: {err}", err=str(exc))
            return {"status": "continue", "feedback": "Evaluator failed, continuing"}

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
            return {"status": "continue", "feedback": "Could not parse evaluator response"}

    async def _run_postprocessing(self, sdk_session_id: str | None) -> None:
        """Run adapted post-processing pipeline (episodic + git only).

        Args:
            sdk_session_id: The SDK session ID from the background task execution
        """
        if sdk_session_id is None:
            _log.warning("No SDK session ID, skipping post-processing")
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
