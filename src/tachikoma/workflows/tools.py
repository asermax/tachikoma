"""Workflow MCP tools for the agent.

Provides MCP tools for managing workflow lifecycle:
- start_workflow: Start a new workflow instance
- update_workflow_state: Transition workflow step states
- get_workflow_state: Query workflow state for recovery
- end_workflow: Complete or abort a workflow
- list_active_workflows: List all active workflows for recovery

Follows DES-006 (MCP tool server factory pattern).
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel, ValidationError

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.session_context import SessionContext
from tachikoma.skills.registry import SkillRegistry, render_skill_block
from tachikoma.workflows.conditions import ConditionResult, evaluate_condition
from tachikoma.workflows.definition import StepDefinition
from tachikoma.workflows.errors import WorkflowRepositoryError
from tachikoma.workflows.model import (
    STEP_COMPLETED,
    STEP_PENDING,
    STEP_SKIPPED,
    STEP_STARTED,
    StepState,
    WorkflowState,
)
from tachikoma.workflows.repository import WorkflowStateRepository

_log = logger.bind(component="workflow_tools")


# ---------------------------------------------------------------------------
# Pydantic models for MCP tool args
# ---------------------------------------------------------------------------


class StartWorkflowArgs(BaseModel):
    skill_name: str
    workflow_name: str


class UpdateWorkflowStateArgs(BaseModel):
    workflow_id: str
    step: str
    action: Literal["start", "complete", "skip"]


class GetWorkflowStateArgs(BaseModel):
    workflow_id: str


class EndWorkflowArgs(BaseModel):
    workflow_id: str
    action: Literal["complete", "abort"]


class ListActiveWorkflowsArgs(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(message: str) -> dict:
    return {"is_error": True, "content": [{"type": "text", "text": message}]}


def _repo_error_response(exc: WorkflowRepositoryError, base_message: str) -> dict:
    cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
    return _error_response(f"{base_message}{cause}")


def _validate_args(args: dict, model: type[BaseModel]):
    try:
        return model.model_validate(args), None
    except ValidationError as exc:
        return None, _error_response(f"Invalid arguments: {exc}")


def _delete_scratchpad(scratchpad_path: str) -> None:
    path = Path(scratchpad_path)
    if path.exists():
        path.unlink()


def _not_found_error(workflow_id: str) -> dict:
    return _error_response(f"Workflow '{workflow_id}' not found or no longer active.")


def _step_to_snapshot(step: StepDefinition) -> dict:
    """Convert a StepDefinition to a snapshot dict for storage."""
    return {
        "id": step.id,
        "title": step.title,
        "required": step.required,
        "path": str(step.instructions_path.parent),
        "required_skills": list(step.required_skills),
        "condition": step.condition,
    }


def _find_next_pending_step(
    step_states: dict[str, StepState],
    definition_snapshot: list[dict],
) -> str | None:
    """Find the next pending step in definition order."""
    for step_def in definition_snapshot:
        step_id = step_def["id"]
        if step_states.get(step_id) == STEP_PENDING:
            return step_id

    return None


async def _evaluate_and_advance(
    step_states: dict[str, StepState],
    definition_snapshot: list[dict],
    scratchpad_path: str,
    workspace_path: Path,
    agent_defaults: AgentDefaults,
    session_context: SessionContext,
) -> tuple[str | None, list[tuple[str, ConditionResult]]]:
    """Evaluate conditions on pending steps and return the next step to start.

    Loops through pending steps in definition order.  Steps without a
    condition start immediately.  Steps with a condition are evaluated;
    if the condition fails, the step is marked as skipped and the loop
    continues.

    Returns:
        (next_step_id, skipped_list) where skipped_list contains
        (step_id, ConditionResult) pairs for condition-skipped steps.
        next_step_id is None when no passing step remains.
    """
    skipped: list[tuple[str, ConditionResult]] = []

    while True:
        next_step_id = _find_next_pending_step(step_states, definition_snapshot)

        if next_step_id is None:
            return None, skipped

        step_info = _get_step_from_snapshot(definition_snapshot, next_step_id)
        condition = step_info.get("condition")

        if not condition:
            return next_step_id, skipped

        result = await evaluate_condition(
            condition_prompt=condition,
            step_states=step_states,
            scratchpad_path=scratchpad_path,
            workspace_path=workspace_path,
            agent_defaults=agent_defaults,
            sdk_session_id=session_context.get(),
        )

        if result.passes:
            return next_step_id, skipped

        step_states[next_step_id] = STEP_SKIPPED
        skipped.append((next_step_id, result))
        _log.info(
            "Condition-skipped step: step={step}, reason={reason}",
            step=next_step_id,
            reason=result.reason,
        )


def _get_step_from_snapshot(
    definition_snapshot: list[dict],
    step_id: str,
) -> dict:
    """Get a step's info from the definition snapshot."""
    for step in definition_snapshot:
        if step["id"] == step_id:
            return step

    return {}


def _read_step_instructions(step_info: dict) -> str | None:
    """Read the instructions.md content for a step."""
    step_path = step_info.get("path")

    if not step_path:
        return None

    instructions_path = Path(step_path) / "instructions.md"

    try:
        return instructions_path.read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _render_required_skills(step_info: dict, registry: SkillRegistry) -> str:
    """Render declared skills for a step's tool-response injection.

    Reads `required_skills` from the step snapshot, resolves each anchor
    through `SkillRegistry.resolve_chain` (deps-first, cycle-tolerant,
    unknown-dep-tolerant, memoized), and deduplicates across anchors so
    shared transitive deps appear once.

    Returns a trailing markdown block with one XML-tagged skill per
    resolved chain entry, or an empty string when no skills resolve
    (no declarations, or all anchors unknown).
    """
    required = step_info.get("required_skills") or []

    if not required:
        return ""

    seen: set[str] = set()
    ordered_blocks: list[str] = []

    for anchor in required:
        try:
            chain = registry.resolve_chain(anchor)
        except KeyError:
            _log.debug(
                "Step declares unknown required skill, skipping: anchor={anchor}",
                anchor=anchor,
            )
            continue

        for skill in chain:
            if skill.name in seen:
                continue
            seen.add(skill.name)
            ordered_blocks.append(render_skill_block(skill))

    if not ordered_blocks:
        return ""

    return "\n\n---\n\n## Required Skills\n\n" + "\n\n".join(ordered_blocks)


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------


def validate_transition(
    step_states: dict[str, StepState],
    step_id: str,
    action: Literal["start", "complete", "skip"],
    definition_snapshot: list[dict],
) -> str | None:
    """Validate a workflow state transition.

    Returns None if valid, error message string if invalid.
    """
    step_def = None
    for step in definition_snapshot:
        if step["id"] == step_id:
            step_def = step
            break

    if step_def is None:
        valid_ids = [s["id"] for s in definition_snapshot]
        return f"Invalid step '{step_id}'. Valid steps: {', '.join(valid_ids)}"

    current_state = step_states.get(step_id)

    if current_state in (STEP_COMPLETED, STEP_SKIPPED):
        return (
            f"Step '{step_id}' is already {current_state}. "
            "Cannot change a completed or skipped step."
        )

    if action == "start":
        if current_state != STEP_PENDING:
            return f"Step '{step_id}' is already {current_state}. Can only start a pending step."

    elif action == "complete":
        if current_state != STEP_STARTED:
            return (
                f"Step '{step_id}' is {current_state or STEP_PENDING}. "
                "Must start a step before completing it."
            )

    elif action == "skip":
        is_required = step_def.get("required", True)
        if "required" not in step_def and "skippable" in step_def:
            is_required = not step_def["skippable"]
        if is_required:
            return f"Step '{step_id}' is required and cannot be skipped."
        if current_state != STEP_PENDING:
            return f"Step '{step_id}' is {current_state}. Can only skip a pending step."

    return None


# ---------------------------------------------------------------------------
# Extracted handler functions (testable without SDK)
# ---------------------------------------------------------------------------


async def handle_start_workflow(
    skill_name: str,
    workflow_name: str,
    registry: SkillRegistry,
    repository: WorkflowStateRepository,
    workspace_path: Path,
) -> dict:
    """Handle start_workflow: create a new workflow instance."""

    workflow_def = registry.get_workflow(skill_name, workflow_name)

    if workflow_def is None:
        return {
            "is_error": True,
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Workflow '{workflow_name}' not found in skill '{skill_name}'. "
                        "Check that the skill exists and contains this workflow."
                    ),
                }
            ],
        }

    if not workflow_def.steps:
        return {
            "is_error": True,
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Workflow '{workflow_name}' has no steps. "
                        "Add step directories with instructions.md files."
                    ),
                }
            ],
        }

    existing = await repository.get_active(skill_name, workflow_name)
    if existing is not None:
        return {
            "is_error": True,
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Workflow '{workflow_name}' is already active for skill "
                        f"'{skill_name}'. Existing workflow ID: {existing.id}. "
                        "Use end_workflow to complete or abort it before starting a new one."
                    ),
                }
            ],
        }

    workflow_id = str(uuid4())
    now = datetime.now(UTC)

    scratchpad_dir = workspace_path / ".tachikoma" / "scratchpads"
    scratchpad_dir.mkdir(parents=True, exist_ok=True)
    scratchpad_path = scratchpad_dir / f"workflow-{workflow_id}.md"
    scratchpad_path.write_text(f"# Workflow: {workflow_name}\n\nWorkflow ID: {workflow_id}\n")

    definition_snapshot = [_step_to_snapshot(step) for step in workflow_def.steps]
    step_states: dict[str, StepState] = {step.id: STEP_PENDING for step in workflow_def.steps}

    state = WorkflowState(
        id=workflow_id,
        skill_name=skill_name,
        workflow_name=workflow_name,
        current_step=None,
        step_states=step_states,
        definition_snapshot=definition_snapshot,
        scratchpad_path=str(scratchpad_path),
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )

    try:
        await repository.create(state)
    except WorkflowRepositoryError as exc:
        scratchpad_path.unlink(missing_ok=True)
        return _repo_error_response(exc, "Failed to create workflow state.")

    step_lines = []
    for i, step in enumerate(workflow_def.steps, 1):
        skip_marker = " (skippable)" if not step.required else ""
        cond_marker = f" (if: {step.condition})" if step.condition else ""
        step_lines.append(f"{i}. **{step.title}** (`{step.id}`){skip_marker}{cond_marker}")

    steps_text = "\n".join(step_lines)

    guidance = (
        f"Workflow started: **{workflow_name}**\n\n"
        f"## Steps\n\n{steps_text}\n\n"
        f"## Getting Started\n\n"
        f'1. Call `update_workflow_state` with `workflow_id="{workflow_id}", '
        f'step="<step_id>", action="start"` to begin the first step\n'
        f"2. Use TodoWrite to create tasks for each step above\n"
        f"3. Read the scratchpad file at `{scratchpad_path}` first, then use Edit "
        f"to update it with your workflow ID (`{workflow_id}`) and progress notes\n\n"
        f"## Progressing\n\n"
        f'- Use `action="start"` to begin the first step (returns its instructions)\n'
        f'- Use `action="complete"` to finish a started step — this **auto-starts** '
        f"the next step and returns its instructions (no separate start call needed)\n"
        f'- Use `action="skip"` to skip a skippable step — also auto-starts the next step\n'
        f"- When the last step is completed, the workflow is **auto-finalized** "
        f"(no need to call `end_workflow`)\n\n"
        f"## Recovery\n\n"
        f"If you lose context, call `list_active_workflows()` to find your "
        f"workflow, then `get_workflow_state()` to resume."
    )

    return {"content": [{"type": "text", "text": guidance}]}


async def handle_update_workflow_state(
    workflow_id: str,
    step: str,
    action: Literal["start", "complete", "skip"],
    repository: WorkflowStateRepository,
    skill_registry: SkillRegistry,
    agent_defaults: AgentDefaults | None = None,
    session_context: SessionContext | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Handle update_workflow_state: transition a step's state."""

    state = await repository.get(workflow_id)

    if state is None:
        return _not_found_error(workflow_id)

    error = validate_transition(state.step_states, step, action, state.definition_snapshot)
    if error is not None:
        return _error_response(error)

    new_step_states = dict(state.step_states)
    new_current_step: str | None = None
    condition_skipped: list[tuple[str, ConditionResult]] = []

    if action == "start":
        # Evaluate condition before starting (if condition support available)
        if agent_defaults and session_context and workspace_path:
            step_info = _get_step_from_snapshot(state.definition_snapshot, step)
            condition = step_info.get("condition")

            if condition:
                result = await evaluate_condition(
                    condition_prompt=condition,
                    step_states=dict(state.step_states),
                    scratchpad_path=state.scratchpad_path,
                    workspace_path=workspace_path,
                    agent_defaults=agent_defaults,
                    sdk_session_id=session_context.get(),
                )

                if not result.passes:
                    # Condition not met — skip and auto-advance
                    new_step_states[step] = STEP_SKIPPED
                    condition_skipped.append((step, result))

                    new_current_step, advance_skipped = await _evaluate_and_advance(
                        new_step_states,
                        state.definition_snapshot,
                        state.scratchpad_path,
                        workspace_path,
                        agent_defaults,
                        session_context,
                    )
                    condition_skipped.extend(advance_skipped)

                    if new_current_step is not None:
                        new_step_states[new_current_step] = STEP_STARTED
                else:
                    new_step_states[step] = STEP_STARTED
                    new_current_step = step
            else:
                new_step_states[step] = STEP_STARTED
                new_current_step = step
        else:
            new_step_states[step] = STEP_STARTED
            new_current_step = step

    elif action in ("complete", "skip"):
        new_step_states[step] = STEP_COMPLETED if action == "complete" else STEP_SKIPPED

        if agent_defaults and session_context and workspace_path:
            new_current_step, advance_skipped = await _evaluate_and_advance(
                new_step_states,
                state.definition_snapshot,
                state.scratchpad_path,
                workspace_path,
                agent_defaults,
                session_context,
            )
            condition_skipped.extend(advance_skipped)
        else:
            new_current_step = _find_next_pending_step(new_step_states, state.definition_snapshot)

        if new_current_step is not None:
            new_step_states[new_current_step] = STEP_STARTED

    try:
        updated = await repository.update(
            workflow_id,
            step_states=new_step_states,
            current_step=new_current_step,
        )
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to update workflow state.")

    if updated is None:
        return _error_response(f"Workflow '{workflow_id}' not found during update.")

    all_done = all(s in (STEP_COMPLETED, STEP_SKIPPED) for s in new_step_states.values())

    # Auto-finalize: soft-delete and clean up scratchpad on completion
    if all_done:
        completed = sum(1 for s in new_step_states.values() if s == STEP_COMPLETED)
        skipped = sum(1 for s in new_step_states.values() if s == STEP_SKIPPED)

        await repository.soft_delete(workflow_id)
        _delete_scratchpad(state.scratchpad_path)

        finalize_text = (
            f"Workflow **{state.workflow_name}** complete and finalized! "
            f"All steps finished ({completed} completed, {skipped} skipped)."
        )

        if condition_skipped:
            skip_lines = [
                f"- `{sid}`: {cr.reason}"
                for sid, cr in condition_skipped
            ]
            finalize_text += "\n\n### Condition-Skipped Steps\n" + "\n".join(skip_lines)

        return {"content": [{"type": "text", "text": finalize_text}]}

    # Build response text
    response_parts: list[str] = []

    if condition_skipped:
        skip_lines = [
            f"- `{sid}`: {cr.reason}"
            for sid, cr in condition_skipped
        ]
        response_parts.append("### Condition-Skipped Steps\n" + "\n".join(skip_lines))

    if action == "start" and step not in [s[0] for s in condition_skipped]:
        step_info = _get_step_from_snapshot(state.definition_snapshot, step)
        instructions = _read_step_instructions(step_info)
        step_path = step_info.get("path", "")

        step_text = f"Step **{step_info['title']}** (`{step}`) started."

        if instructions:
            step_text += f"\n\n{instructions}"

        step_text += _render_required_skills(step_info, skill_registry)

        if step_path:
            step_text += f"\n\n---\n*Step path: `{step_path}`*"

        if response_parts:
            step_text += "\n\n" + "\n\n".join(response_parts)
        return {"content": [{"type": "text", "text": step_text}]}

    # Condition caused the explicitly-started step to be skipped
    if action == "start" and condition_skipped and new_current_step:
            next_info = _get_step_from_snapshot(state.definition_snapshot, new_current_step)
            next_instructions = _read_step_instructions(next_info)
            next_path = next_info.get("path", "")

            next_text = (
                f"Step `{step}` condition-skipped. "
                f"Next step **{next_info['title']}** (`{new_current_step}`) started."
            )

            if next_instructions:
                next_text += f"\n\n{next_instructions}"

            next_text += _render_required_skills(next_info, skill_registry)

            if next_path:
                next_text += f"\n\n---\n*Step path: `{next_path}`*"

            next_text += "\n\n" + "\n\n".join(response_parts)
            return {"content": [{"type": "text", "text": next_text}]}

    # complete/skip with a next step — it's already auto-started
    if new_current_step and action in ("complete", "skip"):
        next_info = _get_step_from_snapshot(state.definition_snapshot, new_current_step)
        next_instructions = _read_step_instructions(next_info)
        next_path = next_info.get("path", "")

        next_text = (
            f"Step `{step}` {action}d. "
            f"Next step **{next_info['title']}** (`{new_current_step}`) started."
        )

        if next_instructions:
            next_text += f"\n\n{next_instructions}"

        next_text += _render_required_skills(next_info, skill_registry)

        if next_path:
            next_text += f"\n\n---\n*Step path: `{next_path}`*"

        if response_parts:
            next_text += "\n\n" + "\n\n".join(response_parts)
        return {"content": [{"type": "text", "text": next_text}]}

    base_text = f"Step `{step}` {action}d."
    if response_parts:
        base_text += "\n\n" + "\n\n".join(response_parts)

    return {"content": [{"type": "text", "text": base_text}]}


async def handle_get_workflow_state(
    workflow_id: str,
    repository: WorkflowStateRepository,
) -> dict:
    """Handle get_workflow_state: return full workflow state."""

    state = await repository.get(workflow_id)

    if state is None:
        return _not_found_error(workflow_id)

    steps_display = []
    for step_def in state.definition_snapshot:
        step_id = step_def["id"]
        status = state.step_states.get(step_id, "pending")
        steps_display.append(f"- **{step_def['title']}** (`{step_id}`): {status}")

    steps_text = "\n".join(steps_display)
    current = state.current_step or "none"

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"## Workflow State\n\n"
                    f"- **ID**: {state.id}\n"
                    f"- **Skill**: {state.skill_name}\n"
                    f"- **Workflow**: {state.workflow_name}\n"
                    f"- **Current Step**: {current}\n"
                    f"- **Scratchpad**: `{state.scratchpad_path}`\n"
                    f"- **Created**: {state.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"- **Updated**: {state.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                    f"### Steps\n\n{steps_text}"
                ),
            }
        ],
    }


async def handle_end_workflow(
    workflow_id: str,
    action: Literal["complete", "abort"],
    repository: WorkflowStateRepository,
    workspace_path: Path,
) -> dict:
    """Handle end_workflow: soft-delete workflow and clean up scratchpad."""

    state = await repository.get(workflow_id)

    if state is None:
        return _not_found_error(workflow_id)

    deleted = await repository.soft_delete(workflow_id)

    if not deleted:
        return _error_response(f"Failed to end workflow '{workflow_id}'.")

    _delete_scratchpad(state.scratchpad_path)

    action_label = "completed" if action == "complete" else "aborted"

    return {
        "content": [
            {
                "type": "text",
                "text": f"Workflow **{state.workflow_name}** {action_label}. State cleaned up.",
            }
        ],
    }


async def handle_list_active_workflows(
    repository: WorkflowStateRepository,
) -> dict:
    """Handle list_active_workflows: return all active workflows."""

    try:
        active = await repository.list_active()
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to list active workflows.")

    if not active:
        return {"content": [{"type": "text", "text": "No active workflows."}]}

    lines = ["## Active Workflows\n"]
    for wf in active:
        current = wf.current_step or "none"
        started = wf.created_at.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"- **{wf.workflow_name}** (skill: `{wf.skill_name}`) "
            f"— ID: `{wf.id}`, current step: `{current}`, started: {started}"
        )

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ---------------------------------------------------------------------------
# MCP tool server factory
# ---------------------------------------------------------------------------


def create_workflow_tools_server(
    repository: WorkflowStateRepository,
    skill_registry: SkillRegistry,
    workspace_path: Path,
    agent_defaults: AgentDefaults | None = None,
    session_context: SessionContext | None = None,
) -> McpSdkServerConfig:
    """Create an MCP server exposing workflow management tools.

    Args:
        repository: The WorkflowStateRepository for state persistence.
        skill_registry: The SkillRegistry for workflow definition lookup.
        workspace_path: The workspace root path for scratchpad files.
        agent_defaults: Shared SDK options for condition evaluation.
        session_context: Shared session ID for condition evaluation forking.

    Returns:
        McpSdkServerConfig for registration with ClaudeAgentOptions.mcp_servers.
    """

    @tool(
        "start_workflow",
        "Start a new workflow instance.\n"
        "\n"
        "Parameters:\n"
        "- skill_name (str, required): Name of the skill containing the workflow\n"
        "- workflow_name (str, required): Name of the workflow to start\n"
        "\n"
        "Creates a tracked workflow instance with a unique ID. Returns step list, "
        "scratchpad path, and guidance for progressing through the workflow.",
        StartWorkflowArgs.model_json_schema(),
    )
    async def start_workflow(args: dict) -> dict:
        parsed, err = _validate_args(args, StartWorkflowArgs)
        if err:
            return err

        return await handle_start_workflow(
            parsed.skill_name,
            parsed.workflow_name,
            skill_registry,
            repository,
            workspace_path,
        )

    @tool(
        "update_workflow_state",
        "Update a workflow step's state.\n"
        "\n"
        "Parameters:\n"
        "- workflow_id (str, required): The workflow instance ID\n"
        "- step (str, required): The step identifier (directory name)\n"
        "- action (str, required): 'start', 'complete', or 'skip'\n"
        "\n"
        "Validates the transition and returns step instructions. "
        "Completing or skipping a step auto-starts the next pending step "
        "and returns its instructions. When all steps are done, the workflow "
        "is auto-finalized (cleaned up).",
        UpdateWorkflowStateArgs.model_json_schema(),
    )
    async def update_workflow_state(args: dict) -> dict:
        parsed, err = _validate_args(args, UpdateWorkflowStateArgs)
        if err:
            return err

        return await handle_update_workflow_state(
            parsed.workflow_id,
            parsed.step,
            parsed.action,
            repository,
            skill_registry,
            agent_defaults=agent_defaults,
            session_context=session_context,
            workspace_path=workspace_path,
        )

    @tool(
        "get_workflow_state",
        "Get the current state of a workflow.\n"
        "\n"
        "Parameters:\n"
        "- workflow_id (str, required): The workflow instance ID\n"
        "\n"
        "Returns full state including all step statuses for recovery after context loss.",
        GetWorkflowStateArgs.model_json_schema(),
    )
    async def get_workflow_state(args: dict) -> dict:
        parsed, err = _validate_args(args, GetWorkflowStateArgs)
        if err:
            return err

        return await handle_get_workflow_state(parsed.workflow_id, repository)

    @tool(
        "end_workflow",
        "End a workflow instance.\n"
        "\n"
        "Parameters:\n"
        "- workflow_id (str, required): The workflow instance ID\n"
        "- action (str, required): 'complete' or 'abort'\n"
        "\n"
        "Primarily used to abort a workflow in progress. Normal completion "
        "is handled automatically when the last step is completed. "
        "Soft-deletes the workflow state and removes the scratchpad file.",
        EndWorkflowArgs.model_json_schema(),
    )
    async def end_workflow(args: dict) -> dict:
        parsed, err = _validate_args(args, EndWorkflowArgs)
        if err:
            return err

        return await handle_end_workflow(
            parsed.workflow_id,
            parsed.action,
            repository,
            workspace_path,
        )

    @tool(
        "list_active_workflows",
        "List all active workflow instances.\n"
        "\n"
        "No parameters required.\n"
        "\n"
        "Returns all non-completed workflows for recovery after context loss.",
        ListActiveWorkflowsArgs.model_json_schema(),
    )
    async def list_active_workflows(args: dict) -> dict:
        return await handle_list_active_workflows(repository)

    return create_sdk_mcp_server(
        name="workflow-tools",
        tools=[
            start_workflow,
            update_workflow_state,
            get_workflow_state,
            end_workflow,
            list_active_workflows,
        ],
    )
