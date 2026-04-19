"""Background task execution for task subsystem.

This module contains:
- background_task_runner: async loop that picks up and executes pending background tasks
- BackgroundTaskExecutor: executes a single background task with evaluator loop
"""

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bubus import EventBus
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from claude_agent_sdk.types import AssistantMessage, ResultMessage, SystemPromptPreset, TextBlock
from loguru import logger

from tachikoma.adapter import sanitize_text
from tachikoma.agent_defaults import AgentDefaults
from tachikoma.buffer.priority import Priority
from tachikoma.config import TaskSettings
from tachikoma.coordinator import _derive_transcript_path
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
from tachikoma.sdk_query import StderrAccumulator
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


async def _sweep_expired_waiters(
    repository: TaskRepository,
    settings: TaskSettings,
    bus: EventBus,
) -> None:
    """Fail waiting instances that have exceeded the wait_timeout."""
    expired = await repository.list_expired_waiting_instances(settings.wait_timeout)

    for instance in expired:
        reason = f"Task timed out waiting for user input after {settings.wait_timeout}s"

        # Resolve notification source
        definition = (
            await repository.get_definition(instance.definition_id)
            if instance.definition_id
            else None
        )
        source = (
            f"Background task: {definition.name}"
            if definition
            else f"Background task: {instance.prompt[:100]}"
        )

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

    if expired:
        _log.info(
            "Expired {count} waiting instances past timeout",
            count=len(expired),
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

## Asking questions

Your messages are not delivered directly to the user — they pass through an evaluator that classifies your output. If you genuinely need user input to proceed, ask your question clearly in plain text. The evaluator will route your question to the user via a notification, and the user's response will arrive as the next conversation turn. You can ask questions multiple times if needed."""  # noqa: E501

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

3. **Clarifying question**: Did the agent ask a question and is waiting for an answer instead of proceeding?
   → {{"status": "needs_input", "rationale": "The exact question the agent asked"}}

4. **Mid-workflow**: Is the agent still working — it announced next steps but hasn't executed them yet, or it's partway through a multi-step process?
   → {{"status": "continue", "rationale": "What the agent said it would do next but has not yet executed"}}

Respond with ONLY a JSON object (no other text, no markdown formatting)."""  # noqa: E501


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
            # Sweep expired waiting instances
            await _sweep_expired_waiters(repository, settings, bus)

            # Query ready instances (pending + waiting-with-response)
            ready_instances = await repository.get_ready_background_instances()

            for instance in ready_instances:
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

        Branches on entry status:
        - "waiting": resume path (re-enter with saved SDK session + user response)
        - "pending": fresh path (standard execution from scratch)
        """
        if instance.status == "waiting":
            await self._execute_resume_path(instance)
            return

        await self._execute_fresh_path(instance)

    async def _execute_fresh_path(self, instance: TaskInstance) -> None:
        """Execute a fresh (pending) background task instance."""
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
            stderr_acc = StderrAccumulator()
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
                stderr=stderr_acc,
            )

            # Execute with evaluator loop
            async with ClaudeSDKClient(options) as client:
                # Initial query
                await client.query(preprocessing_result.prompt)

                await self._run_evaluator_loop(
                    client,
                    instance,
                    notification_source,
                    stderr_acc,
                    None,
                )

        except asyncio.CancelledError:
            _log.info("Background task {inst_id} cancelled", inst_id=instance.id)
            await self._fail_instance(instance.id, "Task cancelled")
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

    async def _execute_resume_path(self, instance: TaskInstance) -> None:
        """Resume a waiting task instance with the user's response.

        Guards: missing user_response, missing sdk_session_id, missing transcript.
        Atomically consumes the response and transitions to running.
        """
        # Defensive guards
        if instance.user_response is None:
            await self._fail_instance(
                instance.id, "Resume path entered without a pending response"
            )
            await dispatch_notification(
                self._bus,
                f"Background task: {instance.prompt[:100]}",
                "Task failed: internal error — no response to resume with",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )
            return

        if instance.sdk_session_id is None:
            await self._fail_instance(
                instance.id, "Cannot resume — missing sdk_session_id"
            )
            await dispatch_notification(
                self._bus,
                f"Background task: {instance.prompt[:100]}",
                "Task failed: no session to resume from",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )
            return

        # Transcript existence guard
        transcript_path = _derive_transcript_path(instance.sdk_session_id, self._cwd)
        if not Path(transcript_path).exists():
            reason = f"Transcript for resume not found: {transcript_path}"
            await self._fail_instance(instance.id, reason)
            await dispatch_notification(
                self._bus,
                f"Background task: {instance.prompt[:100]}",
                f"Task failed: {reason}",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )
            return

        # Atomically consume response and transition to running
        consumed_response = instance.user_response
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

        try:
            # Get the definition for notification source naming
            definition: TaskDefinition | None = None
            if instance.definition_id:
                definition = await self._repository.get_definition(instance.definition_id)

            notification_source = (
                f"Background task: {definition.name}"
                if definition
                else f"Background task: {instance.prompt[:100]}"
            )

            # Run pre-processing for MCP server construction
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

            stderr_acc = StderrAccumulator()
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
                stderr=stderr_acc,
                resume=instance.sdk_session_id,
            )

            sdk_session_id: str | None = None

            async with ClaudeSDKClient(options) as client:
                await client.query(consumed_response)

                await self._run_evaluator_loop(
                    client,
                    instance,
                    notification_source,
                    stderr_acc,
                    sdk_session_id,
                )

        except asyncio.CancelledError:
            _log.info("Background task {inst_id} cancelled during resume", inst_id=instance.id)
            await self._fail_instance(instance.id, "Task cancelled")
            raise

        except Exception as exc:
            stderr = stderr_acc.get()
            if stderr is not None:
                _log.exception(
                    "Background task {inst_id} failed during resume: {err}, stderr={stderr}",
                    inst_id=instance.id,
                    err=str(exc),
                    stderr=stderr,
                )
            else:
                _log.exception(
                    "Background task {inst_id} failed during resume: {err}",
                    inst_id=instance.id,
                    err=str(exc),
                )
            await self._fail_instance(instance.id, str(exc))
            await dispatch_notification(
                self._bus,
                f"Background task: {instance.prompt[:100]}",
                f"Task failed with error: {exc}",
                "error",
                instance.id,
                priority=Priority.URGENT,
            )

    async def _run_evaluator_loop(
        self,
        client: ClaudeSDKClient,
        instance: TaskInstance,
        notification_source: str,
        stderr_acc: StderrAccumulator,
        initial_sdk_session_id: str | None,
    ) -> None:
        """Run the evaluator loop until completion, failure, or waiting transition.

        Shared by both fresh and resume paths.
        """
        sdk_session_id = initial_sdk_session_id
        response_text = ""
        iteration = 0
        max_iterations = self._settings.max_iterations

        while iteration < max_iterations:
            iteration += 1

            # Collect response
            response_chunks: list[str] = []
            async for sdk_message in client.receive_response():
                if isinstance(sdk_message, ResultMessage) and sdk_message.session_id:
                    sdk_session_id = sdk_message.session_id

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
            rationale = eval_result.get("rationale", "")

            _log.debug(
                "Evaluator result for {inst_id}: status={status}",
                inst_id=instance.id,
                status=status,
            )

            if status == "complete":
                await self._complete_instance(instance.id, rationale)
                await self._run_postprocessing(sdk_session_id)
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
                return

            if status == "needs_input":
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
                    "Agent requested input for {inst_id}, transitioning to waiting",
                    inst_id=instance.id,
                )
                await self._repository.update_instance(
                    instance.id,
                    status="waiting",
                    sdk_session_id=sdk_session_id,
                )
                await dispatch_notification(
                    self._bus,
                    notification_source,
                    rationale,
                    "info",
                    instance.id,
                    priority=Priority.URGENT,
                    response_instance_id=instance.id,
                )
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
