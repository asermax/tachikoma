"""Workflow MCP tools for the agent.

Provides MCP tools for managing workflow lifecycle:
- start_workflow: Start a new workflow instance
- update_workflow_state: Transition workflow step states
- get_workflow_state: Query workflow state for recovery
- end_workflow: Complete or abort a workflow
- list_active_workflows: List all active workflows for recovery

Follows DES-006 (MCP tool server factory pattern).
"""

import json
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
    # Declared as a JSON-encoded string (e.g. '["a.md", "b.md"]') rather
    # than a list. The SDK MCP transport's client-side schema validator
    # rejects array-typed arguments, so the tool accepts a JSON string and
    # the wrapper parses it via _decode_items. See DES-006.
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


def _decode_items(raw: str) -> list[str]:
    """Decode the JSON-string form of ``items`` into a list of strings.

    The SDK MCP transport's client-side schema validator rejects array-typed
    tool arguments, so the ``update_workflow_state`` tool accepts ``items`` as
    a JSON string instead. This helper performs the parse-and-validate step.
    See DES-006 for the pattern.

    Raises:
        ValueError: when ``raw`` is not valid JSON, does not encode an array,
            or encodes an array containing non-string items.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"items must be a JSON-encoded array of strings: {exc}") from exc
    if not isinstance(decoded, list):
        raise ValueError(f"items JSON string must encode an array, got {type(decoded).__name__}")
    if not all(isinstance(item, str) for item in decoded):
        raise ValueError("items JSON array must contain only strings")
    return decoded


def delete_scratchpad(scratchpad_path: str) -> None:
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
        "loop": step.loop,
    }


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
        "current_item",
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
        current_item: str | None = None,
    ) -> None:
        self.id = id
        self.workflow_name = workflow_name
        self.skill_name = skill_name
        self.scratchpad_path = scratchpad_path
        self.parent_workflow_id = parent_workflow_id
        self.parent_step_id = parent_step_id
        self.definition_snapshot = definition_snapshot
        self.current_item = current_item

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
) -> list[tuple[str, str | None, str | None]]:
    """Collect (workflow_name, current_step, current_item) per layer for breadcrumb."""
    return [
        (layers[lid].workflow_name, current_steps.get(lid), layers[lid].current_item)
        for lid in chain_order
    ]


def _derive_current_item(
    parent_loop_state: dict | None,
    parent_step_id: str | None,
    parent_current_item: str | None,
) -> str | None:
    """Derive an iteration child's current_item from its parent's loop_state.

    A child is on an iteration when the parent has an entry for ``parent_step_id``
    with a valid index; otherwise it inherits ``parent_current_item`` so
    descendants of an iteration body keep the breadcrumb item suffix.
    """
    if parent_loop_state and parent_step_id:
        entry = parent_loop_state.get(parent_step_id)
        if entry:
            entry_items = entry.get("items", [])
            idx = entry.get("index", 0)
            if 0 <= idx < len(entry_items):
                return entry_items[idx]
    return parent_current_item


def _merge_loop_state(
    current_loop_state: dict | None,
    step_id: str,
    items: list[str],
    index: int,
) -> dict:
    """Merge a loop-state entry into the existing loop_state dict.

    Preserves entries for other step IDs; overwrites/inserts the entry for
    ``step_id``.  Returns a new dict (never mutates the input).
    """
    merged = dict(current_loop_state) if current_loop_state else {}
    merged[step_id] = {"items": list(items), "index": index}
    return merged


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
# Composition spawn helper
# ---------------------------------------------------------------------------


def _try_spawn_child(
    composes_str: str,
    parent: _CascadeLayerInfo,
    parent_step_id: str,
    skill_registry: SkillRegistry,
    current_item: str | None = None,
) -> dict | tuple[_CascadeLayerInfo, str, dict[str, StepState], list[dict]]:
    """Resolve a composition target and prepare a child layer.

    Returns an error dict on failure, or
    ``(child_layer, child_id, child_step_states, child_snapshot)`` on success.
    """
    try:
        target_skill, target_wf = resolve_composes(composes_str, parent.skill_name)
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

    # Inherit parent's current_item when no explicit value supplied (composes
    # spawn).  Loop spawns pass the item explicitly.  Inheriting keeps the
    # breadcrumb suffix visible for descendants of an iteration body.
    effective_item = current_item if current_item is not None else parent.current_item

    child_layer = _CascadeLayerInfo(
        id=child_id,
        workflow_name=target_wf,
        skill_name=target_skill,
        scratchpad_path=parent.scratchpad_path,
        parent_workflow_id=parent.id,
        parent_step_id=parent_step_id,
        definition_snapshot=child_snapshot,
        current_item=effective_item,
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
    items: list[str] | None = None,
) -> (
    dict
    | tuple[
        MutationBatch,
        CascadeOutcome,
        list[tuple[str, str | None, str | None]],
        list[dict],
        str,
    ]
):
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
        deepest.step_states,
        step,
        action,
        deepest.definition_snapshot,
    )
    if transition_error is not None:
        return _error_response(transition_error)

    # Gate 3: structural step-kind / items consistency (before condition eval)
    is_loop_step = bool(step_info.get("loop"))
    if items is not None and action != "start":
        return _error_response("items parameter is only allowed on the 'start' action.")
    if action == "start" and items is not None and not is_loop_step:
        return _error_response("items parameter is not allowed when starting a non-loop step.")

    # ── Initialize mutable state ─────────────────────────────────────────

    batch = MutationBatch()
    condition_skips: list[tuple[str, str, str]] = []

    layers: dict[str, _CascadeLayerInfo] = {}
    mutable_ss: dict[str, dict[str, StepState]] = {}
    mutable_loop_state: dict[str, dict | None] = {}
    current_steps: dict[str, str | None] = {}
    chain_order: list[str] = []

    for state in chain:
        layers[state.id] = _CascadeLayerInfo.from_state(state)
        mutable_ss[state.id] = dict(state.step_states)
        mutable_loop_state[state.id] = (
            dict(state.loop_state) if state.loop_state is not None else None
        )
        current_steps[state.id] = state.current_step
        chain_order.append(state.id)

    # Recover each iteration child's current_item from its parent's loop_state
    # (the live value is not stored on the child record).
    for state in chain:
        if state.parent_workflow_id is None:
            continue
        parent_layer = layers.get(state.parent_workflow_id)
        if parent_layer is None:
            continue
        layers[state.id].current_item = _derive_current_item(
            mutable_loop_state.get(state.parent_workflow_id),
            state.parent_step_id,
            parent_layer.current_item,
        )

    scratchpad_path = chain[0].scratchpad_path

    has_condition_support = (
        agent_defaults is not None and session_context is not None and workspace_path is not None
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
                condition_skips.append((current.workflow_name, step, cond_result.reason))

        if not condition_failed:
            # Gate 5: items required for loop steps (after condition passes)
            if is_loop_step and items is None:
                return _error_response(
                    "items parameter is required when starting a loop step. "
                    "Pass items=[...] (or items=[] to skip with zero iterations)."
                )

            if is_loop_step:
                # Loop-step start
                if items:
                    # Non-empty items: STARTED + spawn iteration 0
                    ss[step] = STEP_STARTED
                    current_steps[current.id] = step
                    new_ls = _merge_loop_state(
                        mutable_loop_state[current.id],
                        step,
                        items,
                        index=0,
                    )
                    mutable_loop_state[current.id] = new_ls
                    batch.ordered.append(
                        UpdateState(
                            layer_id=current.id,
                            step_states=dict(ss),
                            current_step=step,
                            loop_state=new_ls,
                        )
                    )
                    spawn = _try_spawn_child(
                        step_info["loop"],
                        current,
                        step,
                        skill_registry,
                        current_item=items[0],
                    )
                    if isinstance(spawn, dict):
                        return spawn
                    child_layer, child_id, child_ss, child_snapshot = spawn
                    batch.ordered.append(
                        CreateChild(
                            child_id=child_id,
                            parent_id=current.id,
                            parent_step_id=step,
                            skill_name=child_layer.skill_name,
                            workflow_name=child_layer.workflow_name,
                            step_states=dict(child_ss),
                            definition_snapshot=child_snapshot,
                            scratchpad_path=current.scratchpad_path,
                        )
                    )
                    layers[child_id] = child_layer
                    mutable_ss[child_id] = dict(child_ss)
                    mutable_loop_state[child_id] = None
                    current_steps[child_id] = None
                    chain_order.append(child_id)
                    current = child_layer
                else:
                    # Empty items: COMPLETED directly (zero iterations)
                    ss[step] = STEP_COMPLETED
                    current_steps[current.id] = step
                    new_ls = _merge_loop_state(
                        mutable_loop_state[current.id],
                        step,
                        [],
                        index=0,
                    )
                    mutable_loop_state[current.id] = new_ls
                    batch.ordered.append(
                        UpdateState(
                            layer_id=current.id,
                            step_states=dict(ss),
                            current_step=step,
                            loop_state=new_ls,
                        )
                    )
                    # Fall through to auto-advance while-loop
            elif not step_info.get("composes"):
                # Simple start on a regular step — done immediately
                ss[step] = STEP_STARTED
                current_steps[current.id] = step
                batch.ordered.append(
                    UpdateState(
                        layer_id=current.id,
                        step_states=dict(ss),
                        current_step=step,
                    )
                )
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
            else:
                # Composes step — existing behavior
                ss[step] = STEP_STARTED
                current_steps[current.id] = step
                batch.ordered.append(
                    UpdateState(
                        layer_id=current.id,
                        step_states=dict(ss),
                        current_step=step,
                    )
                )

                spawn = _try_spawn_child(
                    step_info["composes"],
                    current,
                    step,
                    skill_registry,
                )
                if isinstance(spawn, dict):
                    return spawn

                child_layer, child_id, child_ss, child_snapshot = spawn
                batch.ordered.append(
                    CreateChild(
                        child_id=child_id,
                        parent_id=current.id,
                        parent_step_id=step,
                        skill_name=child_layer.skill_name,
                        workflow_name=child_layer.workflow_name,
                        step_states=dict(child_ss),
                        definition_snapshot=child_snapshot,
                        scratchpad_path=current.scratchpad_path,
                    )
                )

                layers[child_id] = child_layer
                mutable_ss[child_id] = dict(child_ss)
                current_steps[child_id] = None
                chain_order.append(child_id)
                current = child_layer

    elif action == "complete":
        ss[step] = STEP_COMPLETED

    elif action == "skip":
        ss[step] = STEP_SKIPPED

    while True:
        ss = mutable_ss[current.id]
        snapshot = current.definition_snapshot

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
                condition_skips.append((current.workflow_name, sid, cr.reason))
        else:
            next_step = _find_next_pending_step(ss, snapshot)

        if next_step is None:
            if current.parent_workflow_id is not None:
                batch.ordered.append(
                    UpdateState(
                        layer_id=current.id,
                        step_states=dict(ss),
                        current_step=None,
                    )
                )
                batch.ordered.append(SoftDelete(layer_id=current.id))

                parent_id = current.parent_workflow_id
                assert parent_id is not None
                parent = layers[parent_id]
                assert current.parent_step_id is not None
                parent_step_id = current.parent_step_id
                parent_snapshot = parent.definition_snapshot
                parent_step_info = _get_step_from_snapshot(parent_snapshot, parent_step_id)

                chain_order.remove(current.id)

                if parent_step_info.get("loop"):
                    # Loop iteration — advance or exhaust
                    current_ls = (mutable_loop_state[parent_id] or {}).copy()
                    entry = current_ls.get(parent_step_id, {})
                    items_list = entry.get("items", [])
                    next_index = entry.get("index", 0) + 1

                    if next_index < len(items_list):
                        # Spawn iteration N+1
                        new_loop_state = _merge_loop_state(
                            mutable_loop_state[parent_id],
                            parent_step_id,
                            items_list,
                            index=next_index,
                        )
                        mutable_loop_state[parent_id] = new_loop_state
                        batch.ordered.append(
                            UpdateState(
                                layer_id=parent_id,
                                step_states=dict(mutable_ss[parent_id]),
                                current_step=parent_step_id,
                                loop_state=new_loop_state,
                            )
                        )
                        spawn = _try_spawn_child(
                            parent_step_info["loop"],
                            parent,
                            parent_step_id,
                            skill_registry,
                            current_item=items_list[next_index],
                        )
                        if isinstance(spawn, dict):
                            return spawn
                        child_layer, child_id, child_ss, child_snapshot = spawn
                        batch.ordered.append(
                            CreateChild(
                                child_id=child_id,
                                parent_id=parent_id,
                                parent_step_id=parent_step_id,
                                skill_name=child_layer.skill_name,
                                workflow_name=child_layer.workflow_name,
                                step_states=dict(child_ss),
                                definition_snapshot=child_snapshot,
                                scratchpad_path=parent.scratchpad_path,
                            )
                        )
                        layers[child_id] = child_layer
                        mutable_ss[child_id] = dict(child_ss)
                        mutable_loop_state[child_id] = None
                        current_steps[child_id] = None
                        chain_order.append(child_id)
                        current = child_layer
                        continue
                    else:
                        # Persist final loop_state immediately on exhaustion;
                        # subsequent cascade-up UpdateStates do not re-thread it.
                        mutable_ss[parent_id][parent_step_id] = STEP_COMPLETED
                        new_loop_state = _merge_loop_state(
                            mutable_loop_state[parent_id],
                            parent_step_id,
                            items_list,
                            index=next_index,
                        )
                        mutable_loop_state[parent_id] = new_loop_state
                        batch.ordered.append(
                            UpdateState(
                                layer_id=parent_id,
                                step_states=dict(mutable_ss[parent_id]),
                                current_step=parent_step_id,
                                loop_state=new_loop_state,
                            )
                        )
                        current = parent
                        continue
                else:
                    # Existing composition behavior: mark parent step COMPLETED
                    mutable_ss[parent_id][parent_step_id] = STEP_COMPLETED
                    current = parent
                    continue
            else:
                batch.ordered.append(
                    UpdateState(
                        layer_id=current.id,
                        step_states=dict(ss),
                        current_step=None,
                    )
                )
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

        next_info = _get_step_from_snapshot(snapshot, next_step)
        next_loop = next_info.get("loop")

        if next_loop:
            # HALT: loop step requires explicit start with items.
            # Persist the prior cascade work but leave the loop step PENDING.
            batch.ordered.append(
                UpdateState(
                    layer_id=current.id,
                    step_states=dict(ss),
                    current_step=current_steps.get(current.id),
                )
            )
            return (
                batch,
                CascadeOutcome(
                    deepest_layer_id=current.id,
                    active_step_id=next_step,
                    condition_skips=condition_skips,
                    finalized_top_level=False,
                    halted_at_loop_step=next_step,
                ),
                _build_breadcrumb_parts(layers, chain_order, current_steps),
                current.definition_snapshot,
                scratchpad_path,
            )

        composes = next_info.get("composes")

        if composes:
            ss[next_step] = STEP_STARTED
            current_steps[current.id] = next_step
            batch.ordered.append(
                UpdateState(
                    layer_id=current.id,
                    step_states=dict(ss),
                    current_step=next_step,
                )
            )

            spawn = _try_spawn_child(
                composes,
                current,
                next_step,
                skill_registry,
            )
            if isinstance(spawn, dict):
                return spawn

            child_layer, child_id, child_ss, child_snapshot = spawn
            batch.ordered.append(
                CreateChild(
                    child_id=child_id,
                    parent_id=current.id,
                    parent_step_id=next_step,
                    skill_name=child_layer.skill_name,
                    workflow_name=child_layer.workflow_name,
                    step_states=dict(child_ss),
                    definition_snapshot=child_snapshot,
                    scratchpad_path=current.scratchpad_path,
                )
            )

            layers[child_id] = child_layer
            mutable_ss[child_id] = dict(child_ss)
            current_steps[child_id] = None
            chain_order.append(child_id)
            current = child_layer
            continue

        ss[next_step] = STEP_STARTED
        current_steps[current.id] = next_step
        batch.ordered.append(
            UpdateState(
                layer_id=current.id,
                step_states=dict(ss),
                current_step=next_step,
            )
        )
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
    items: list[str] | None = None,
) -> dict:
    """Handle update_workflow_state: transition a step's state with cascade support."""

    result = await _run_cascade(
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

    batch, outcome, breadcrumb_parts, deepest_snapshot, scratchpad = result

    try:
        await repository.apply_mutation_batch(batch)
    except WorkflowRepositoryError as exc:
        return _repo_error_response(exc, "Failed to update workflow state.")

    if outcome.finalized_top_level:
        delete_scratchpad(scratchpad)

        completed = 0
        skipped = len(outcome.condition_skips)
        for mutation in batch.ordered:
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
        halted_id = outcome.halted_at_loop_step
        halted_info = _get_step_from_snapshot(deepest_snapshot, halted_id)
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

    # Build response for the activated step
    active_step_id = outcome.active_step_id
    assert active_step_id is not None
    step_info = _get_step_from_snapshot(deepest_snapshot, active_step_id)

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

    breadcrumb = _render_breadcrumb(breadcrumb_parts)
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
        "- items (str, optional): Required when starting a loop step. A\n"
        "  JSON-encoded string of opaque references (e.g.\n"
        '  \'["file1.md", "file2.md"]\') that the iteration body interprets.\n'
        "  Rejected when starting a non-loop step. Pass items='[]' to skip a\n"
        "  loop step with zero iterations. The parameter is a JSON string\n"
        "  because the SDK MCP transport cannot reliably pass arrays.\n"
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

        items_list: list[str] | None = None
        if parsed.items is not None:
            try:
                items_list = _decode_items(parsed.items)
            except ValueError as exc:
                return _error_response(str(exc))

        return await handle_update_workflow_state(
            parsed.workflow_id,
            parsed.step,
            parsed.action,
            repository,
            skill_registry,
            agent_defaults=agent_defaults,
            session_context=session_context,
            workspace_path=workspace_path,
            items=items_list,
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
