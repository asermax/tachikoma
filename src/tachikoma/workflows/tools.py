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
from typing import Literal, Self
from uuid import uuid4

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool
from loguru import logger
from pydantic import BaseModel, ValidationError

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.session_context import SessionContext
from tachikoma.skills.registry import SkillRegistry, render_skill_block
from tachikoma.workflows.composition import (
    CascadeOutcome,
    CreateChild,
    MutationBatch,
    SoftDelete,
    UpdateState,
    resolve_composes,
)
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
    Path(scratchpad_path).unlink(missing_ok=True)


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
        "composes": step.composes,
    }


def _render_breadcrumb(parts: list[tuple[str, str | None]]) -> str:
    """Render breadcrumb showing the active path through nested layers.

    Each part is (workflow_name, step_id).  Segments with a ``None`` step
    are omitted (e.g., a layer whose step was just finalized).
    """
    if not parts:
        return ""
    segments = [
        f"{wf}/{sid}" for wf, sid in parts if sid is not None
    ]
    return " > ".join(segments) if segments else ""


class _CascadeLayerInfo:
    """Lightweight layer info used during cascade processing."""

    __slots__ = (
        "id",
        "workflow_name",
        "skill_name",
        "scratchpad_path",
        "parent_workflow_id",
        "parent_step_id",
        "definition_snapshot",
    )

    def __init__(
        self,
        id: str,
        workflow_name: str,
        skill_name: str,
        scratchpad_path: str,
        parent_workflow_id: str | None,
        parent_step_id: str | None,
        definition_snapshot: list[dict],
    ) -> None:
        self.id = id
        self.workflow_name = workflow_name
        self.skill_name = skill_name
        self.scratchpad_path = scratchpad_path
        self.parent_workflow_id = parent_workflow_id
        self.parent_step_id = parent_step_id
        self.definition_snapshot = definition_snapshot

    @classmethod
    def from_state(cls, state: WorkflowState) -> Self:
        return cls(
            id=state.id,
            workflow_name=state.workflow_name,
            skill_name=state.skill_name,
            scratchpad_path=state.scratchpad_path,
            parent_workflow_id=state.parent_workflow_id,
            parent_step_id=state.parent_step_id,
            definition_snapshot=state.definition_snapshot,
        )


def _build_breadcrumb_parts(
    layers: dict[str, _CascadeLayerInfo],
    chain_order: list[str],
    current_steps: dict[str, str | None],
) -> list[tuple[str, str | None]]:
    """Collect (workflow_name, current_step) per layer for breadcrumb rendering."""
    return [
        (layers[lid].workflow_name, current_steps.get(lid))
        for lid in chain_order
    ]


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


def _format_condition_skips(
    condition_skipped: list[tuple[str, ConditionResult]],
) -> str:
    skip_lines = [f"- `{sid}`: {cr.reason}" for sid, cr in condition_skipped]
    return "### Condition-Skipped Steps\n" + "\n".join(skip_lines)


def _format_cascade_skips(
    condition_skips: list[tuple[str, str, str]],
) -> str:
    """Format condition-skipped steps from the cascade (workflow_name, step_id, reason)."""
    skip_lines = [
        f"- `{wf}/{sid}`: {reason}" for wf, sid, reason in condition_skips
    ]
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
# Composition spawn helper
# ---------------------------------------------------------------------------


def _try_spawn_child(
    composes_str: str,
    parent: _CascadeLayerInfo,
    parent_step_id: str,
    skill_registry: SkillRegistry,
) -> dict | tuple[_CascadeLayerInfo, str, dict[str, StepState], list[dict]]:
    """Resolve a composition target and prepare a child layer.

    Returns an error dict on failure, or
    ``(child_layer, child_id, child_step_states, child_snapshot)`` on success.
    """
    try:
        target_skill, target_wf = resolve_composes(
            composes_str, parent.skill_name
        )
    except ValueError:
        return _error_response(
            f"Composition step has invalid 'composes' value: '{composes_str}'. "
            "Abort the workflow to clean up."
        )

    child_def = skill_registry.get_workflow(target_skill, target_wf)
    if child_def is None:
        return _error_response(
            f"Composition target '{target_skill}/{target_wf}' no longer exists. "
            "The skill may have been reloaded. Abort the workflow to clean up."
        )

    if not child_def.steps:
        return _error_response(
            f"Composition target '{target_skill}/{target_wf}' has no steps. "
            "Abort the workflow to clean up."
        )

    child_id = str(uuid4())
    child_snapshot = [_step_to_snapshot(s) for s in child_def.steps]
    child_ss: dict[str, StepState] = {s.id: STEP_PENDING for s in child_def.steps}

    child_layer = _CascadeLayerInfo(
        id=child_id,
        workflow_name=target_wf,
        skill_name=target_skill,
        scratchpad_path=parent.scratchpad_path,
        parent_workflow_id=parent.id,
        parent_step_id=parent_step_id,
        definition_snapshot=child_snapshot,
    )

    return child_layer, child_id, child_ss, child_snapshot


# ---------------------------------------------------------------------------
# Cascade engine
# ---------------------------------------------------------------------------


async def _run_cascade(
    workflow_id: str,
    step: str,
    action: Literal["start", "complete", "skip"],
    repository: WorkflowStateRepository,
    skill_registry: SkillRegistry,
    agent_defaults: AgentDefaults | None,
    session_context: SessionContext | None,
    workspace_path: Path | None,
) -> dict | tuple[
    MutationBatch, CascadeOutcome,
    list[tuple[str, str | None]], list[dict], str,
]:
    """Run the cascade-aware activation loop.

    Returns an error dict on validation/routing failure, or a 5-tuple
    ``(batch, outcome, breadcrumb_parts, deepest_snapshot, scratchpad_path)``
    on success.
    """
    # ── Read chain and validate routing ──────────────────────────────────

    chain = await repository.get_active_chain(workflow_id)
    if not chain:
        return _not_found_error(workflow_id)

    # Child-ID rejection
    if chain[0].parent_workflow_id is not None:
        return _error_response(
            f"Workflow '{workflow_id}' is a composed child. "
            "Operate on its top-level workflow instead."
        )

    deepest = chain[-1]

    # Deepest-active routing validation
    step_info = _get_step_from_snapshot(deepest.definition_snapshot, step)
    if not step_info:
        valid_ids = [s["id"] for s in deepest.definition_snapshot]
        return _error_response(
            f"Invalid step '{step}'. The deepest active layer is "
            f"'{deepest.workflow_name}'. Valid steps: {', '.join(valid_ids)}."
        )

    transition_error = validate_transition(
        deepest.step_states, step, action, deepest.definition_snapshot,
    )
    if transition_error is not None:
        return _error_response(transition_error)

    # ── Initialize mutable state ─────────────────────────────────────────

    batch = MutationBatch()
    condition_skips: list[tuple[str, str, str]] = []

    layers: dict[str, _CascadeLayerInfo] = {}
    mutable_ss: dict[str, dict[str, StepState]] = {}
    current_steps: dict[str, str | None] = {}
    chain_order: list[str] = []

    for state in chain:
        layers[state.id] = _CascadeLayerInfo.from_state(state)
        mutable_ss[state.id] = dict(state.step_states)
        current_steps[state.id] = state.current_step
        chain_order.append(state.id)

    scratchpad_path = chain[0].scratchpad_path

    has_condition_support = (
        agent_defaults is not None
        and session_context is not None
        and workspace_path is not None
    )

    # ── Apply the requested action ───────────────────────────────────────

    current = layers[deepest.id]
    ss = mutable_ss[deepest.id]

    if action == "start":
        condition = step_info.get("condition")
        condition_failed = False

        if condition and has_condition_support:
            assert agent_defaults is not None
            assert session_context is not None
            assert workspace_path is not None

            cond_result = await evaluate_condition(
                condition_prompt=condition,
                step_states=dict(ss),
                scratchpad_path=current.scratchpad_path,
                workspace_path=workspace_path,
                agent_defaults=agent_defaults,
                sdk_session_id=session_context.get(),
            )

            if not cond_result.passes:
                condition_failed = True
                ss[step] = STEP_SKIPPED
                condition_skips.append(
                    (current.workflow_name, step, cond_result.reason)
                )

        if not condition_failed:
            ss[step] = STEP_STARTED

            if not step_info.get("composes"):
                # Simple start on a regular step — done immediately
                current_steps[current.id] = step
                batch.ordered.append(UpdateState(
                    layer_id=current.id,
                    step_states=dict(ss),
                    current_step=step,
                ))
                return (
                    batch,
                    CascadeOutcome(
                        deepest_layer_id=current.id,
                        active_step_id=step,
                        condition_skips=condition_skips,
                        finalized_top_level=False,
                    ),
                    _build_breadcrumb_parts(layers, chain_order, current_steps),
                    current.definition_snapshot,
                    scratchpad_path,
                )

            # Composition step started — queue update, spawn child
            current_steps[current.id] = step
            batch.ordered.append(UpdateState(
                layer_id=current.id,
                step_states=dict(ss),
                current_step=step,
            ))

            spawn = _try_spawn_child(
                step_info["composes"], current, step, skill_registry,
            )
            if isinstance(spawn, dict):
                return spawn

            child_layer, child_id, child_ss, child_snapshot = spawn
            batch.ordered.append(CreateChild(
                child_id=child_id,
                parent_id=current.id,
                parent_step_id=step,
                skill_name=child_layer.skill_name,
                workflow_name=child_layer.workflow_name,
                step_states=dict(child_ss),
                definition_snapshot=child_snapshot,
                scratchpad_path=current.scratchpad_path,
            ))

            layers[child_id] = child_layer
            mutable_ss[child_id] = dict(child_ss)
            current_steps[child_id] = None
            chain_order.append(child_id)
            current = child_layer
            # Fall through to advance loop to start child's first step
        else:
            pass  # Fall through to advance loop

    elif action == "complete":
        ss[step] = STEP_COMPLETED

    elif action == "skip":
        ss[step] = STEP_SKIPPED

    # ── Advance loop ─────────────────────────────────────────────────────

    while True:
        ss = mutable_ss[current.id]
        snapshot = current.definition_snapshot

        # Find next pending step (with condition evaluation if available)
        next_step: str | None = None
        if has_condition_support:
            assert agent_defaults is not None
            assert session_context is not None
            assert workspace_path is not None

            next_step, extra_skips = await _evaluate_and_advance(
                step_states=ss,
                definition_snapshot=snapshot,
                scratchpad_path=current.scratchpad_path,
                workspace_path=workspace_path,
                agent_defaults=agent_defaults,
                session_context=session_context,
            )
            for sid, cr in extra_skips:
                condition_skips.append(
                    (current.workflow_name, sid, cr.reason)
                )
        else:
            next_step = _find_next_pending_step(ss, snapshot)

        if next_step is None:
            # No more steps in this layer → auto-finalize
            if current.parent_workflow_id is not None:
                # Child layer: queue finalize + pop to parent
                batch.ordered.append(UpdateState(
                    layer_id=current.id,
                    step_states=dict(ss),
                    current_step=None,
                ))
                batch.ordered.append(SoftDelete(layer_id=current.id))

                parent_id = current.parent_workflow_id
                assert parent_id is not None
                parent = layers[parent_id]
                assert current.parent_step_id is not None
                mutable_ss[parent_id][current.parent_step_id] = STEP_COMPLETED

                chain_order.remove(current.id)
                current = parent
                continue
            else:
                # Top-level finalized
                batch.ordered.append(UpdateState(
                    layer_id=current.id,
                    step_states=dict(ss),
                    current_step=None,
                ))
                batch.ordered.append(SoftDelete(layer_id=current.id))
                return (
                    batch,
                    CascadeOutcome(
                        deepest_layer_id=current.id,
                        active_step_id=None,
                        condition_skips=condition_skips,
                        finalized_top_level=True,
                    ),
                    [],
                    current.definition_snapshot,
                    scratchpad_path,
                )

        # Found next step — check for composition
        next_info = _get_step_from_snapshot(snapshot, next_step)
        composes = next_info.get("composes")

        if composes:
            # Start the composition step + spawn child
            ss[next_step] = STEP_STARTED
            current_steps[current.id] = next_step
            batch.ordered.append(UpdateState(
                layer_id=current.id,
                step_states=dict(ss),
                current_step=next_step,
            ))

            spawn = _try_spawn_child(
                composes, current, next_step, skill_registry,
            )
            if isinstance(spawn, dict):
                return spawn

            child_layer, child_id, child_ss, child_snapshot = spawn
            batch.ordered.append(CreateChild(
                child_id=child_id,
                parent_id=current.id,
                parent_step_id=next_step,
                skill_name=child_layer.skill_name,
                workflow_name=child_layer.workflow_name,
                step_states=dict(child_ss),
                definition_snapshot=child_snapshot,
                scratchpad_path=current.scratchpad_path,
            ))

            layers[child_id] = child_layer
            mutable_ss[child_id] = dict(child_ss)
            current_steps[child_id] = None
            chain_order.append(child_id)
            current = child_layer
            continue

        # Regular step → start it and terminate
        ss[next_step] = STEP_STARTED
        current_steps[current.id] = next_step
        batch.ordered.append(UpdateState(
            layer_id=current.id,
            step_states=dict(ss),
            current_step=next_step,
        ))
        return (
            batch,
            CascadeOutcome(
                deepest_layer_id=current.id,
                active_step_id=next_step,
                condition_skips=condition_skips,
                finalized_top_level=False,
            ),
            _build_breadcrumb_parts(layers, chain_order, current_steps),
            current.definition_snapshot,
            scratchpad_path,
        )


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
        compose_marker = f" (composes: {step.composes})" if step.composes else ""
        step_lines.append(
            f"{i}. **{step.title}** (`{step.id}`)"
            f"{skip_marker}{cond_marker}{compose_marker}"
        )

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
    """Handle update_workflow_state: transition a step's state with cascade support."""

    result = await _run_cascade(
        workflow_id, step, action,
        repository, skill_registry,
        agent_defaults, session_context, workspace_path,
    )

    if isinstance(result, dict):
        return result

    batch, outcome, breadcrumb_parts, deepest_snapshot, scratchpad = result

    try:
        await repository.apply_mutation_batch(batch)
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to update workflow state.")

    if outcome.finalized_top_level:
        _delete_scratchpad(scratchpad)

        completed = 0
        skipped = len(outcome.condition_skips)
        for mutation in batch.ordered:
            if isinstance(mutation, UpdateState):
                completed += sum(
                    1 for s in mutation.step_states.values() if s == STEP_COMPLETED
                )
                skipped += sum(
                    1 for s in mutation.step_states.values() if s == STEP_SKIPPED
                )

        text = (
            "Workflow complete and finalized! "
            f"All steps finished ({completed} completed, {skipped} skipped)."
        )
        if outcome.condition_skips:
            text += "\n\n" + _format_cascade_skips(outcome.condition_skips)

        return {"content": [{"type": "text", "text": text}]}

    # Build response for the activated step
    active_step_id = outcome.active_step_id
    assert active_step_id is not None
    step_info = _get_step_from_snapshot(deepest_snapshot, active_step_id)

    original_skipped = any(
        sid == step for _, sid, _ in outcome.condition_skips
    )

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

    breadcrumb = _render_breadcrumb(breadcrumb_parts)
    if breadcrumb:
        response["content"][0]["text"] = (
            breadcrumb + "\n\n" + response["content"][0]["text"]
        )

    return response


async def handle_get_workflow_state(
    workflow_id: str,
    repository: WorkflowStateRepository,
) -> dict:
    """Handle get_workflow_state: return full workflow state with nested view."""

    chain = await repository.get_active_chain(workflow_id)

    if not chain:
        # Maybe it's a child ID — try direct get for backwards compat
        state = await repository.get(workflow_id)
        if state is None:
            return _not_found_error(workflow_id)

        steps_display = [
            f"- **{sd['title']}** (`{sd['id']}`): "
            f"{state.step_states.get(sd['id'], 'pending')}"
            for sd in state.definition_snapshot
        ]

        text = (
            f"## Workflow State\n\n"
            f"- **ID**: {state.id}\n"
            f"- **Skill**: {state.skill_name}\n"
            f"- **Workflow**: {state.workflow_name}\n"
            f"- **Current Step**: {state.current_step or 'none'}\n"
            f"- **Scratchpad**: `{state.scratchpad_path}`\n"
            f"- **Created**: {state.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"- **Updated**: {state.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            f"### Steps\n\n" + "\n".join(steps_display) + "\n\n"
            "> This is a composed child. Access via the top-level workflow "
            "for the full nested view."
        )
        if state.parent_workflow_id:
            text += f"\n> Parent workflow ID: `{state.parent_workflow_id}`"

        return {"content": [{"type": "text", "text": text}]}

    top = chain[0]

    # Single layer — standard view
    if len(chain) == 1:
        state = top
        steps_display = [
            f"- **{sd['title']}** (`{sd['id']}`): "
            f"{state.step_states.get(sd['id'], 'pending')}"
            for sd in state.definition_snapshot
        ]

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"## Workflow State\n\n"
                    f"- **ID**: {state.id}\n"
                    f"- **Skill**: {state.skill_name}\n"
                    f"- **Workflow**: {state.workflow_name}\n"
                    f"- **Current Step**: {state.current_step or 'none'}\n"
                    f"- **Scratchpad**: `{state.scratchpad_path}`\n"
                    f"- **Created**: {state.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
                    f"- **Updated**: {state.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
                    f"### Steps\n\n" + "\n".join(steps_display)
                ),
            }],
        }

    # Multi-layer — nested view with breadcrumb
    breadcrumb_parts = [
        f"{layer.workflow_name}/{layer.current_step}"
        for layer in chain
        if layer.current_step
    ]
    breadcrumb = " > ".join(breadcrumb_parts)

    top_steps = [
        f"- **{sd['title']}** (`{sd['id']}`): "
        f"{top.step_states.get(sd['id'], 'pending')}"
        for sd in top.definition_snapshot
    ]

    text = (
        f"## Workflow State\n\n"
        f"- **ID**: {top.id}\n"
        f"- **Skill**: {top.skill_name}\n"
        f"- **Workflow**: {top.workflow_name}\n"
        f"- **Current Step**: {top.current_step or 'none'}\n"
        f"- **Scratchpad**: `{top.scratchpad_path}`\n"
        f"- **Created**: {top.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"- **Updated**: {top.updated_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"### Steps\n\n" + "\n".join(top_steps)
    )

    for child in chain[1:]:
        child_steps = [
            f"  - **{sd['title']}** (`{sd['id']}`): "
            f"{child.step_states.get(sd['id'], 'pending')}"
            for sd in child.definition_snapshot
        ]
        text += (
            f"\n\n### Active Child: {child.workflow_name}\n\n"
            f"- **ID**: {child.id}\n"
            f"- **Current Step**: {child.current_step or 'none'}\n\n"
            f"#### Steps\n\n" + "\n".join(child_steps)
        )

    if breadcrumb:
        text = f"> {breadcrumb}\n\n" + text

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
            f"Workflow '{workflow_id}' is a composed child. "
            "End its top-level workflow instead."
        )

    ids = await repository.abort_cascade(workflow_id)

    if not ids:
        return _error_response(f"Failed to end workflow '{workflow_id}'.")

    _delete_scratchpad(state.scratchpad_path)

    action_label = "completed" if action == "complete" else "aborted"
    count_text = (
        f" ({len(ids)} records cleaned up)" if len(ids) > 1 else ""
    )

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
