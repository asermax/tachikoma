"""Workflow MCP tools for the agent.

Provides MCP tools for managing workflow lifecycle:
- start_workflow: Start a new workflow instance
- update_workflow_state: Transition workflow step states (kept for handler reuse)
- get_workflow_state: Query workflow state for recovery
- end_workflow: Complete or abort a workflow (kept for handler reuse)
- list_active_workflows: List all active workflows for recovery

Follows DES-006 (MCP tool server factory pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel, ValidationError

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.session_context import SessionContext
from tachikoma.skills.registry import SkillRegistry, render_skill_block
from tachikoma.workflows.cascade import (
    CascadeResult,
    _derive_current_item,
    _error_response,
    _get_step_from_snapshot,
    _not_found_error,
    _step_to_snapshot,
    run_cascade,
)
from tachikoma.workflows.composition import UpdateState, resolve_composes
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

if TYPE_CHECKING:
    from tachikoma.tasks.repository import TaskRepository

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
    items: str | None = None


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


def _repo_error_response(exc: WorkflowRepositoryError, base_message: str) -> dict:
    cause = f" Cause: {exc.__cause__}" if exc.__cause__ else ""
    return _error_response(f"{base_message}{cause}")


def _validate_args(args: dict, model: type[BaseModel]):
    try:
        return model.model_validate(args), None
    except ValidationError as exc:
        return None, _error_response(f"Invalid arguments: {exc}")


def delete_scratchpad(scratchpad_path: str) -> None:
    Path(scratchpad_path).unlink(missing_ok=True)


def _render_breadcrumb(parts: list[tuple[str, str | None, str | None]]) -> str:
    """Render breadcrumb showing the active path through nested layers.

    Each part is (workflow_name, step_id, current_item).  Segments with a
    ``None`` step are omitted.  The deepest segment gets an ``(item: <value>)``
    suffix when ``current_item`` is set.
    """
    if not parts:
        return ""
    segments = []
    for i, (wf, sid, item) in enumerate(parts):
        if sid is None:
            continue
        seg = f"{wf}/{sid}"
        is_deepest = i == len(parts) - 1
        if is_deepest and item is not None:
            seg += f" (item: {item})"
        segments.append(seg)
    return " > ".join(segments) if segments else ""


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


def _format_cascade_skips(
    condition_skips: list[tuple[str, str, str]],
) -> str:
    """Format condition-skipped steps from the cascade (workflow_name, step_id, reason)."""
    skip_lines = [f"- `{wf}/{sid}`: {reason}" for wf, sid, reason in condition_skips]
    return "### Condition-Skipped Steps\n" + "\n".join(skip_lines)


def _build_step_response(
    step_info: dict,
    prefix: str,
    skill_registry: SkillRegistry,
    extra_parts: list[str] | None = None,
) -> dict:
    text = prefix
    instructions = _read_step_instructions(step_info)
    if instructions:
        text += f"\n\n{instructions}"
    text += _render_required_skills(step_info, skill_registry)
    step_path = step_info.get("path", "")
    if step_path:
        text += f"\n\n---\n*Step path: `{step_path}`*"
    if extra_parts:
        text += "\n\n" + "\n\n".join(extra_parts)
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Extracted handler functions (testable without SDK)
# ---------------------------------------------------------------------------


async def handle_start_workflow(
    skill_name: str,
    workflow_name: str,
    registry: SkillRegistry,
    repository: WorkflowStateRepository,
    workspace_path: Path,
    task_repository: TaskRepository | None = None,
) -> dict:
    """Handle start_workflow: create a new workflow instance and enqueue first step."""
    from tachikoma.tasks.model import TaskInstance  # noqa: PLC0415

    workflow_def = registry.get_workflow(skill_name, workflow_name)

    if workflow_def is None:
        return _error_response(
            f"Workflow '{workflow_name}' not found in skill '{skill_name}'. "
            "Check that the skill exists and contains this workflow."
        )

    if not workflow_def.steps:
        return _error_response(
            f"Workflow '{workflow_name}' has no steps. "
            "Add step directories with instructions.md files."
        )

    existing = await repository.get_active(skill_name, workflow_name)
    if existing is not None:
        return _error_response(
            f"Workflow '{workflow_name}' is already active for skill "
            f"'{skill_name}'. Existing workflow ID: {existing.id}. "
            "Use end_workflow to complete or abort it before starting a new one."
        )

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

    # Enqueue the first step as a background TaskInstance
    if task_repository is not None:
        first_step = workflow_def.steps[0]
        instance = TaskInstance(
            id=str(uuid4()),
            task_type="background",
            status="pending",
            prompt=first_step.id,
            scheduled_for=now,
            workflow_id=workflow_id,
            definition_id=None,
        )
        try:
            await task_repository.create_instance(instance)
        except Exception as exc:
            _log.warning(
                "Failed to enqueue first workflow step: {err}",
                err=str(exc),
            )

    step_lines = []
    for i, step in enumerate(workflow_def.steps, 1):
        skip_marker = " (skippable)" if not step.required else ""
        cond_marker = f" (if: {step.condition})" if step.condition else ""
        compose_marker = f" (composes: {step.composes})" if step.composes else ""
        loop_marker = f" (loop: {step.loop})" if step.loop else ""
        step_lines.append(
            f"{i}. **{step.title}** (`{step.id}`)"
            f"{skip_marker}{cond_marker}{compose_marker}{loop_marker}"
        )

    steps_text = "\n".join(step_lines)

    guidance = (
        f"Workflow started: **{workflow_name}**\n\n"
        f"## Steps\n\n{steps_text}\n\n"
        f"Workflow ID: `{workflow_id}`\n"
        f"Scratchpad: `{scratchpad_path}`\n\n"
        f"The first step has been enqueued as a background task and will "
        f"execute autonomously. Use `get_workflow_state()` or "
        f"`list_active_workflows()` to monitor progress."
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
    items: list[str] | None = None,
) -> dict:
    """Handle update_workflow_state: transition a step's state with cascade support."""

    result = await run_cascade(
        workflow_id,
        step,
        action,
        repository,
        skill_registry,
        agent_defaults,
        session_context,
        workspace_path,
        items=items,
    )

    if isinstance(result, dict):
        return result

    cascade: CascadeResult = result

    try:
        await repository.apply_mutation_batch(cascade.mutations)
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to update workflow state.")

    outcome = cascade.outcome

    if outcome.finalized_top_level:
        delete_scratchpad(cascade.scratchpad_path)

        completed = 0
        skipped = len(outcome.condition_skips)
        for mutation in cascade.mutations.ordered:
            if isinstance(mutation, UpdateState):
                completed += sum(1 for s in mutation.step_states.values() if s == STEP_COMPLETED)
                skipped += sum(1 for s in mutation.step_states.values() if s == STEP_SKIPPED)

        text = (
            "Workflow complete and finalized! "
            f"All steps finished ({completed} completed, {skipped} skipped)."
        )
        if outcome.condition_skips:
            text += "\n\n" + _format_cascade_skips(outcome.condition_skips)

        return {"content": [{"type": "text", "text": text}]}

    # Handle auto-start halt at loop step
    if outcome.halted_at_loop_step is not None:
        halted_info = cascade.next_step_info or {}
        halted_id = outcome.halted_at_loop_step
        halted_title = halted_info.get("title", halted_id)
        text = (
            f"Step `{step}` {action}d.\n\n"
            f"The next step **{halted_title}** (`{halted_id}`) is a loop step. "
            f'Call `update_workflow_state(workflow_id="{workflow_id}", '
            f'step="{halted_id}", action="start", items=[...])` '
            "to begin iterating, or `items=[]` to skip with zero iterations."
        )
        if outcome.condition_skips:
            text += "\n\n" + _format_cascade_skips(outcome.condition_skips)
        return {"content": [{"type": "text", "text": text}]}

    # Handle auto-start halt at condition step
    if outcome.halted_at_condition_step is not None:
        halted_info = cascade.next_step_info or {}
        halted_id = outcome.halted_at_condition_step
        halted_title = halted_info.get("title", halted_id)
        condition = halted_info.get("condition")
        text = (
            f"Step `{step}` {action}d.\n\n"
            f"The next step **{halted_title}** (`{halted_id}`) has a condition "
            f"to evaluate:\n\n"
            f"**Condition**: {condition}\n\n"
            f"Evaluate this condition based on the current context.\n"
            f'- If the condition passes: call `update_workflow_state('
            f'workflow_id="{workflow_id}", step="{halted_id}", action="start")`\n'
            f'- If the condition does not pass: call `update_workflow_state('
            f'workflow_id="{workflow_id}", step="{halted_id}", action="skip")`'
        )
        return {"content": [{"type": "text", "text": text}]}

    # Build response for the activated step
    active_step_id = outcome.active_step_id
    assert active_step_id is not None
    step_info = cascade.next_step_info or _get_step_from_snapshot(
        cascade.deepest_snapshot, active_step_id
    )

    original_skipped = any(sid == step for _, sid, _ in outcome.condition_skips)

    if action == "start" and not original_skipped and active_step_id == step:
        prefix = f"Step **{step_info['title']}** (`{active_step_id}`) started."
    elif active_step_id is not None:
        if action == "start" and original_skipped:
            prefix = (
                f"Step `{step}` condition-skipped. "
                f"Next step **{step_info['title']}** (`{active_step_id}`) started."
            )
        elif action == "start":
            prefix = (
                f"Step `{step}` started. "
                f"Next step **{step_info['title']}** (`{active_step_id}`) started."
            )
        else:
            prefix = (
                f"Step `{step}` {action}d. "
                f"Next step **{step_info['title']}** (`{active_step_id}`) started."
            )
    else:
        prefix = f"Step `{step}` {action}d."

    extra_parts = []
    if outcome.condition_skips:
        extra_parts.append(_format_cascade_skips(outcome.condition_skips))

    response = _build_step_response(step_info, prefix, skill_registry, extra_parts)

    breadcrumb = _render_breadcrumb(cascade.breadcrumb_parts)
    if breadcrumb:
        response["content"][0]["text"] = breadcrumb + "\n\n" + response["content"][0]["text"]

    return response


def _detect_corrupted_composition_targets(
    chain: list[WorkflowState],
    skill_registry: SkillRegistry | None,
) -> list[tuple[str, str, str]]:
    """Return (workflow_name, step_id, target) tuples for active composition
    steps whose target is no longer registered (R5 fourth AC).

    A composition step is considered "corrupted" when its `composes` target
    cannot be resolved in the current skill registry AND the step is in
    ``STEP_STARTED`` (i.e. the parent is mid-spawn / mid-run for that step).
    Pending or completed composition steps are not flagged here — pending
    will surface the corruption on activation, completed has nothing to do.
    """
    if skill_registry is None:
        return []

    corrupted: list[tuple[str, str, str]] = []
    for layer in chain:
        for step_def in layer.definition_snapshot:
            composes = step_def.get("composes")
            if not composes:
                continue
            step_id = step_def["id"]
            if layer.step_states.get(step_id) != STEP_STARTED:
                continue
            try:
                target_skill, target_wf = resolve_composes(composes, layer.skill_name)
            except ValueError:
                corrupted.append((layer.workflow_name, step_id, composes))
                continue
            if skill_registry.get_workflow(target_skill, target_wf) is None:
                corrupted.append((layer.workflow_name, step_id, f"{target_skill}/{target_wf}"))
    return corrupted


def _format_corruption_warning(corrupted: list[tuple[str, str, str]]) -> str:
    lines = ["> ⚠️  **Workflow definition corruption detected.**"]
    lines.append(">")
    for wf_name, step_id, target in corrupted:
        lines.append(
            f"> - Step `{wf_name}/{step_id}` references `{target}`, which is no longer registered."
        )
    lines.append(">")
    lines.append(
        "> The active workflow cannot proceed safely. "
        "Abort the parent workflow with `end_workflow(action='abort')`."
    )
    return "\n".join(lines)


def _render_state_view(state: WorkflowState, *, header: str = "Workflow State") -> str:
    steps_display = [
        f"- **{sd['title']}** (`{sd['id']}`): {state.step_states.get(sd['id'], STEP_PENDING)}"
        for sd in state.definition_snapshot
    ]

    loop_blocks = _render_loop_step_blocks(state)

    return (
        f"## {header}\n\n"
        f"- **ID**: {state.id}\n"
        f"- **Skill**: {state.skill_name}\n"
        f"- **Workflow**: {state.workflow_name}\n"
        f"- **Current Step**: {state.current_step or 'none'}\n"
        f"- **Scratchpad**: `{state.scratchpad_path}`\n"
        f"- **Created**: {state.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"- **Updated**: {state.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"### Steps\n\n" + "\n".join(steps_display) + (f"\n\n{loop_blocks}" if loop_blocks else "")
    )


def _render_loop_step_blocks(state: WorkflowState) -> str:
    """Render `### Loop step` blocks for each active loop step on a layer."""
    if not state.loop_state:
        return ""

    blocks: list[str] = []
    for step_id, entry in state.loop_state.items():
        items = entry.get("items", [])
        index = entry.get("index", 0)
        step_info = _get_step_from_snapshot(state.definition_snapshot, step_id)
        step_title = step_info.get("title", step_id)
        count = len(items)

        if count == 0:
            items_line = "Items (0): (none) — completed with zero iterations"
        else:
            items_formatted = ", ".join(f"`{i}`" for i in items)
            items_line = f"Items ({count}): {items_formatted}"

        if index >= count:
            iteration_line = f"Current iteration: {count} / {count} (complete)"
        else:
            iteration_line = f"Current iteration: {index + 1} / {count}"
            current_item = items[index] if index < count else None
            if current_item is not None:
                iteration_line += f"\n- Current item: `{current_item}`"

        blocks.append(
            f"### Loop step: {step_title} (`{step_id}`)\n\n- {items_line}\n- {iteration_line}"
        )

    return "\n\n".join(blocks)


async def handle_get_workflow_state(
    workflow_id: str,
    repository: WorkflowStateRepository,
    skill_registry: SkillRegistry | None = None,
) -> dict:
    """Handle get_workflow_state: return full workflow state with nested view.

    When ``workflow_id`` resolves to a composed child, returns a standalone
    view with a note pointing at the parent (R12 fifth AC).  When the
    registry is provided and an active composition step's target is no
    longer registered, prepends a corruption warning (R5 fourth AC).
    """

    chain = await repository.get_active_chain(workflow_id)

    if not chain:
        return _not_found_error(workflow_id)

    head = chain[0]

    # Child-ID call — backwards-compat standalone view with a note
    if head.parent_workflow_id is not None:
        text = _render_state_view(head)
        text += (
            "\n\n> This is a composed child. Access via the top-level workflow "
            "for the full nested view."
        )
        text += f"\n> Parent workflow ID: `{head.parent_workflow_id}`"
        return {"content": [{"type": "text", "text": text}]}

    corrupted = _detect_corrupted_composition_targets(chain, skill_registry)

    # Single layer — standard view
    if len(chain) == 1:
        text = _render_state_view(head)
        if corrupted:
            text = _format_corruption_warning(corrupted) + "\n\n" + text
        return {"content": [{"type": "text", "text": text}]}

    # Multi-layer — nested view with breadcrumb.
    current_item_by_id: dict[str, str | None] = {}
    for i, layer in enumerate(chain):
        if i == 0:
            current_item_by_id[layer.id] = None
            continue
        parent = chain[i - 1]
        current_item_by_id[layer.id] = _derive_current_item(
            parent.loop_state,
            layer.parent_step_id,
            current_item_by_id.get(parent.id),
        )

    breadcrumb = _render_breadcrumb(
        [
            (layer.workflow_name, layer.current_step, current_item_by_id.get(layer.id))
            for layer in chain
        ]
    )

    text = _render_state_view(head)

    for child in chain[1:]:
        child_steps = [
            f"  - **{sd['title']}** (`{sd['id']}`): {child.step_states.get(sd['id'], STEP_PENDING)}"
            for sd in child.definition_snapshot
        ]
        text += (
            f"\n\n### Active Child: {child.workflow_name}\n\n"
            f"- **ID**: {child.id}\n"
            f"- **Current Step**: {child.current_step or 'none'}\n\n"
            f"#### Steps\n\n" + "\n".join(child_steps)
        )
        child_loop_blocks = _render_loop_step_blocks(child)
        if child_loop_blocks:
            text += f"\n\n{child_loop_blocks}"

    if breadcrumb:
        text = f"> {breadcrumb}\n\n" + text

    if corrupted:
        text = _format_corruption_warning(corrupted) + "\n\n" + text

    return {"content": [{"type": "text", "text": text}]}


async def handle_end_workflow(
    workflow_id: str,
    action: Literal["complete", "abort"],
    repository: WorkflowStateRepository,
    workspace_path: Path,
) -> dict:
    """Handle end_workflow: abort cascade for top-level workflows."""

    state = await repository.get(workflow_id)

    if state is None:
        return _not_found_error(workflow_id)

    if state.parent_workflow_id is not None:
        return _error_response(
            f"Workflow '{workflow_id}' is a composed child. End its top-level workflow instead."
        )

    ids = await repository.abort_cascade(workflow_id)

    if not ids:
        return _error_response(f"Failed to end workflow '{workflow_id}'.")

    delete_scratchpad(state.scratchpad_path)

    action_label = "completed" if action == "complete" else "aborted"
    count_text = f" ({len(ids)} records cleaned up)" if len(ids) > 1 else ""

    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"Workflow **{state.workflow_name}** {action_label}."
                    f"{count_text} State cleaned up."
                ),
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
    task_repository: TaskRepository | None = None,
) -> McpSdkServerConfig:
    """Create an MCP server exposing workflow management tools.

    Args:
        repository: The WorkflowStateRepository for state persistence.
        skill_registry: The SkillRegistry for workflow definition lookup.
        workspace_path: The workspace root path for scratchpad files.
        agent_defaults: Shared SDK options for condition evaluation.
        session_context: Shared session ID for condition evaluation forking.
        task_repository: TaskRepository for enqueuing first step as TaskInstance.

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
        "Creates a tracked workflow instance with a unique ID and enqueues the "
        "first step as a background task. Returns step list, scratchpad path, "
        "and workflow ID for monitoring.",
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
            task_repository=task_repository,
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

        return await handle_get_workflow_state(
            parsed.workflow_id,
            repository,
            skill_registry,
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
            get_workflow_state,
            list_active_workflows,
        ],
    )
