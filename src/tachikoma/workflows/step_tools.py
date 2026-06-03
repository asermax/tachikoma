"""Workflow step MCP tools for background task execution.

Provides MCP tools available only in workflow step task sessions:
- complete_step: Complete the current step and advance
- skip_step: Skip the current step (only if not required)
- abort_workflow: Abort the entire workflow
- request_input: Ask the user a question and wait for response

Follows DES-006 (MCP tool server factory pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from bubus import EventBus
from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel

from tachikoma.buffer.priority import Priority
from tachikoma.notifications import NotificationCycleState, dispatch_notification
from tachikoma.skills.registry import SkillRegistry
from tachikoma.tasks.model import TaskInstance
from tachikoma.tasks.repository import TaskRepository
from tachikoma.workflows.cascade import (
    CascadeResult,
    _error_response,
    _get_step_from_snapshot,
    _not_found_error,
    run_cascade,
)
from tachikoma.workflows.composition import UpdateState
from tachikoma.workflows.errors import WorkflowRepositoryError
from tachikoma.workflows.model import STEP_COMPLETED, STEP_SKIPPED
from tachikoma.workflows.repository import WorkflowStateRepository

_log = logger.bind(component="workflow_step_tools")

_MAX_HANDOFF_LENGTH = 4000


# ---------------------------------------------------------------------------
# Pydantic models for MCP tool args
# ---------------------------------------------------------------------------


class CompleteStepArgs(BaseModel):
    handoff: str | None = None


class SkipStepArgs(BaseModel):
    pass


class AbortWorkflowArgs(BaseModel):
    pass


class RequestInputArgs(BaseModel):
    question: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_scratchpad(scratchpad_path: str) -> None:
    Path(scratchpad_path).unlink(missing_ok=True)


def _repo_error_response(exc: WorkflowRepositoryError, base_message: str) -> dict:
    cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
    return _error_response(f"{base_message}{cause}")


def _build_notification_source(skill_name: str, workflow_name: str) -> str:
    return f"Workflow: {skill_name}/{workflow_name}"


def _count_step_outcomes(
    cascade: CascadeResult,
) -> tuple[int, int]:
    """Count completed and skipped steps from the final state of each layer.

    Each UpdateState contains the full step_states (not a delta), so summing
    across all mutations would over-count. Instead, keep only the last
    UpdateState per layer and count from those.
    """
    last_per_layer: dict[str, dict] = {}
    for mutation in cascade.mutations.ordered:
        if isinstance(mutation, UpdateState):
            last_per_layer[mutation.layer_id] = mutation.step_states

    completed = 0
    skipped = len(cascade.outcome.condition_skips)
    for ss in last_per_layer.values():
        completed += sum(1 for s in ss.values() if s == STEP_COMPLETED)
        skipped += sum(1 for s in ss.values() if s == STEP_SKIPPED)
    return completed, skipped


async def _enqueue_next_step(
    next_step_id: str,
    workflow_id: str,
    task_repository: TaskRepository,
) -> dict | None:
    """Create a pending TaskInstance for the next workflow step.

    Returns None on success, or an error dict on failure.
    """
    try:
        instance = TaskInstance(
            id=str(uuid4()),
            task_type="background",
            status="pending",
            prompt=next_step_id,
            scheduled_for=datetime.now(UTC),
            workflow_id=workflow_id,
            definition_id=None,
        )
        await task_repository.create_instance(instance)
        return None
    except Exception as exc:
        return _error_response(f"Failed to enqueue next step: {exc}")


# ---------------------------------------------------------------------------
# Extracted handler functions (testable without SDK)
# ---------------------------------------------------------------------------


async def handle_complete_step(
    workflow_id: str,
    handoff: str | None,
    repository: WorkflowStateRepository,
    task_repository: TaskRepository,
    skill_registry: SkillRegistry,
) -> dict:
    """Complete the current step and advance to the next.

    Validates handoff length, reads workflow state to find the current step,
    runs cascade to determine next step, applies mutations, enqueues next
    step TaskInstance, and stores handoff for the context provider.
    """
    if handoff is not None:
        if len(handoff) > _MAX_HANDOFF_LENGTH:
            return _error_response(
                f"Hand-off message exceeds {_MAX_HANDOFF_LENGTH} characters. "
                f"Current: {len(handoff)}. Please shorten the message."
            )
        if not handoff.strip():
            handoff = None

    chain = await repository.get_active_chain(workflow_id)
    if not chain:
        return _not_found_error(workflow_id)

    deepest = chain[-1]
    current_step = deepest.current_step
    if current_step is None:
        return _error_response("No step is currently active in this workflow.")

    result = await run_cascade(
        workflow_id,
        current_step,
        "complete",
        repository,
        skill_registry,
        agent_defaults=None,
        session_context=None,
        workspace_path=None,
    )

    if isinstance(result, dict):
        return result

    cascade: CascadeResult = result

    try:
        await repository.apply_mutation_batch(cascade.mutations)
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to apply workflow state changes.")

    if handoff is not None and cascade.next_step_id is not None:
        await repository.update_pending_handoff(workflow_id, handoff)

    if cascade.outcome.finalized_top_level:
        _delete_scratchpad(cascade.scratchpad_path)
        completed, skipped = _count_step_outcomes(cascade)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Step completed. Workflow finalized! "
                        f"All steps finished ({completed} completed, {skipped} skipped)."
                    ),
                }
            ]
        }

    if cascade.next_step_id is not None:
        enqueue_error = await _enqueue_next_step(cascade.next_step_id, workflow_id, task_repository)
        if enqueue_error is not None:
            return enqueue_error

    next_info = cascade.next_step_info or {}
    next_title = next_info.get("title", cascade.next_step_id or "unknown")

    if cascade.outcome.halted_at_loop_step:
        text = (
            f"Step `{current_step}` completed.\n\n"
            f"The next step **{next_title}** (`{cascade.next_step_id}`) is a loop step. "
            "It will be handled by the next step agent."
        )
    elif cascade.next_step_id is not None:
        text = (
            f"Step `{current_step}` completed. "
            f"Next step **{next_title}** (`{cascade.next_step_id}`) enqueued."
        )
    else:
        text = f"Step `{current_step}` completed."

    return {"content": [{"type": "text", "text": text}]}


async def handle_skip_step(
    workflow_id: str,
    repository: WorkflowStateRepository,
    task_repository: TaskRepository,
    skill_registry: SkillRegistry,
) -> dict:
    """Skip the current step and advance to the next.

    Validates the step is skippable, runs cascade with skip action,
    applies mutations, enqueues next step TaskInstance.
    """
    chain = await repository.get_active_chain(workflow_id)
    if not chain:
        return _not_found_error(workflow_id)

    deepest = chain[-1]
    current_step = deepest.current_step
    if current_step is None:
        return _error_response("No step is currently active in this workflow.")

    step_info = _get_step_from_snapshot(deepest.definition_snapshot, current_step)
    if step_info:
        is_required = step_info.get("required", True)
        if "required" not in step_info and "skippable" in step_info:
            is_required = not step_info["skippable"]
        has_condition = step_info.get("condition") is not None
        if is_required and not has_condition:
            return _error_response(f"Step '{current_step}' is required and cannot be skipped.")

    result = await run_cascade(
        workflow_id,
        current_step,
        "skip",
        repository,
        skill_registry,
        agent_defaults=None,
        session_context=None,
        workspace_path=None,
    )

    if isinstance(result, dict):
        return result

    cascade: CascadeResult = result

    try:
        await repository.apply_mutation_batch(cascade.mutations)
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to apply workflow state changes.")

    if cascade.outcome.finalized_top_level:
        _delete_scratchpad(cascade.scratchpad_path)
        completed, skipped = _count_step_outcomes(cascade)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Step skipped. Workflow finalized! "
                        f"All steps finished ({completed} completed, {skipped} skipped)."
                    ),
                }
            ]
        }

    if cascade.next_step_id is not None:
        enqueue_error = await _enqueue_next_step(cascade.next_step_id, workflow_id, task_repository)
        if enqueue_error is not None:
            return enqueue_error

    next_info = cascade.next_step_info or {}
    next_title = next_info.get("title", cascade.next_step_id or "unknown")

    if cascade.next_step_id is not None:
        text = (
            f"Step `{current_step}` skipped. "
            f"Next step **{next_title}** (`{cascade.next_step_id}`) enqueued."
        )
    else:
        text = f"Step `{current_step}` skipped."

    return {"content": [{"type": "text", "text": text}]}


async def handle_abort_workflow(
    workflow_id: str,
    repository: WorkflowStateRepository,
) -> dict:
    """Abort the entire workflow immediately.

    Soft-deletes workflow state and all descendants, removes the scratchpad.
    """
    state = await repository.get(workflow_id)
    if state is None:
        return _not_found_error(workflow_id)

    ids = await repository.abort_cascade(workflow_id)
    if not ids:
        return _error_response(f"Failed to abort workflow '{workflow_id}'.")

    _delete_scratchpad(state.scratchpad_path)

    count_text = f" ({len(ids)} records cleaned up)" if len(ids) > 1 else ""
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Workflow **{state.workflow_name}** aborted.{count_text} State cleaned up."
                ),
            }
        ]
    }


async def handle_request_input(
    question: str,
    notification_source: str,
    instance_id: str,
    bus: EventBus,
    cycle_state: NotificationCycleState | None = None,
) -> dict:
    """Ask the user a question and wait for their response.

    Dispatches a notification with await_response=true, which pauses
    execution until the user replies via respond_to_task.
    """
    if not question.strip():
        return {
            "is_error": True,
            "content": [{"type": "text", "text": "Question cannot be empty."}],
        }

    await dispatch_notification(
        bus,
        notification_source,
        question,
        "info",
        source_id=instance_id,
        priority=Priority.URGENT,
        response_instance_id=instance_id,
    )

    if cycle_state is not None:
        cycle_state.await_response_requested = True

    return {
        "content": [
            {
                "type": "text",
                "text": "Question sent to user. Execution paused until they respond.",
            }
        ]
    }


# ---------------------------------------------------------------------------
# MCP tool server factory (DES-006)
# ---------------------------------------------------------------------------


def create_workflow_step_tools_server(
    repository: WorkflowStateRepository,
    task_repository: TaskRepository,
    skill_registry: SkillRegistry,
    bus: EventBus,
    workflow_id: str,
    instance_id: str,
    cycle_state: NotificationCycleState | None = None,
    notification_source: str | None = None,
) -> McpSdkServerConfig:
    """Create an MCP server exposing workflow step tools.

    Tools are available only during workflow step task execution.
    The factory closure captures per-instance dependencies.

    Args:
        repository: WorkflowStateRepository for state management.
        task_repository: TaskRepository for enqueuing next step instances.
        skill_registry: SkillRegistry for cascade composition resolution.
        bus: EventBus for notification dispatch.
        workflow_id: The top-level workflow state ID.
        instance_id: The current TaskInstance ID (for response routing).
        cycle_state: Shared notification cycle state for await_response signaling.
        notification_source: Pre-built source string (e.g. "Workflow: skill/name").

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """
    source = notification_source or f"Workflow: {workflow_id}"

    @tool(
        "complete_step",
        "Complete this workflow step and advance to the next one.\n"
        "\n"
        "Parameters:\n"
        "- handoff (str, optional): A summary message (max 4000 chars) relayed to "
        "the next step's agent. Use it to pass key context, decisions made, and "
        "pointers to scratchpad details. Omit or pass empty string for no hand-off.\n"
        "\n"
        "When all steps are complete, the workflow is auto-finalized.",
        CompleteStepArgs.model_json_schema(),
    )
    async def complete_step(args: dict) -> dict:
        parsed = CompleteStepArgs.model_validate(args)
        return await handle_complete_step(
            workflow_id,
            parsed.handoff,
            repository,
            task_repository,
            skill_registry,
        )

    @tool(
        "skip_step",
        "Skip this workflow step and advance to the next one.\n"
        "\n"
        "Only works for non-required steps or steps with conditions. "
        "Use this when the step should not be executed based on current context.",
        SkipStepArgs.model_json_schema(),
    )
    async def skip_step(args: dict) -> dict:
        return await handle_skip_step(
            workflow_id,
            repository,
            task_repository,
            skill_registry,
        )

    @tool(
        "abort_workflow",
        "Abort the entire workflow immediately.\n"
        "\n"
        "Soft-deletes all workflow state and removes the scratchpad. "
        "Use this when the workflow cannot or should not continue.",
        AbortWorkflowArgs.model_json_schema(),
    )
    async def abort_workflow(args: dict) -> dict:
        return await handle_abort_workflow(workflow_id, repository)

    @tool(
        "request_input",
        "Ask the user a question and wait for their response.\n"
        "\n"
        "Parameters:\n"
        "- question (str, required): The question to ask the user.\n"
        "\n"
        "Execution pauses until the user replies. Use this when you genuinely "
        "need human input to proceed.",
        RequestInputArgs.model_json_schema(),
    )
    async def request_input(args: dict) -> dict:
        parsed = RequestInputArgs.model_validate(args)
        return await handle_request_input(
            parsed.question,
            source,
            instance_id,
            bus,
            cycle_state,
        )

    return create_sdk_mcp_server(
        name="workflow-step-tools",
        tools=[complete_step, skip_step, abort_workflow, request_input],
    )
