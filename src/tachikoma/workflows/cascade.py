"""Extracted cascade computation for workflow state transitions.

Separates cascade logic (computation) from response formatting (presentation).
The cascade engine handles composition spawning, loop iteration, condition
evaluation, and auto-advance through pending steps.

Returns CascadeResult (computation output) without side effects — callers
apply mutations and enqueue tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self
from uuid import uuid4

from loguru import logger

from tachikoma.skills.registry import SkillRegistry
from tachikoma.workflows.composition import (
    CascadeOutcome,
    CreateChild,
    MutationBatch,
    SoftDelete,
    UpdateState,
    resolve_composes,
)
from tachikoma.workflows.definition import StepDefinition
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
    from tachikoma.agent_defaults import AgentDefaults
    from tachikoma.session_context import SessionContext

_log = logger.bind(component="workflow_cascade")


# ---------------------------------------------------------------------------
# Shared response helpers (used by cascade.py and tools.py)
# ---------------------------------------------------------------------------


def _error_response(message: str) -> dict:
    return {"is_error": True, "content": [{"type": "text", "text": message}]}


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


# ---------------------------------------------------------------------------
# Cascade helpers
# ---------------------------------------------------------------------------


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


def _find_next_step_and_condition(
    step_states: dict[str, StepState],
    definition_snapshot: list[dict],
) -> tuple[str | None, dict | None]:
    """Find the next pending step and return its snapshot dict."""
    for step_def in definition_snapshot:
        if step_states.get(step_def["id"]) == STEP_PENDING:
            return step_def["id"], step_def

    return None, None


def _get_step_from_snapshot(
    definition_snapshot: list[dict],
    step_id: str,
) -> dict:
    """Get a step's info from the definition snapshot."""
    for step in definition_snapshot:
        if step["id"] == step_id:
            return step

    return {}


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
        has_condition = step_def.get("condition") is not None
        if is_required and not has_condition:
            return f"Step '{step_id}' is required and cannot be skipped."
        if current_state != STEP_PENDING:
            return f"Step '{step_id}' is {current_state}. Can only skip a pending step."

    return None


# ---------------------------------------------------------------------------
# CascadeResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CascadeResult:
    """Structured result of cascade computation.

    Wraps the existing CascadeOutcome with convenience fields for consumers.
    Callers apply mutations and enqueue tasks — no side effects in the cascade.
    """

    outcome: CascadeOutcome
    mutations: MutationBatch
    next_step_id: str | None
    next_step_info: dict | None
    handoff_for_next: str | None
    breadcrumb_parts: list[tuple[str, str | None, str | None]]
    deepest_snapshot: list[dict]
    scratchpad_path: str


# ---------------------------------------------------------------------------
# Cascade engine
# ---------------------------------------------------------------------------


async def run_cascade(
    workflow_id: str,
    step: str,
    action: Literal["start", "complete", "skip"],
    repository: WorkflowStateRepository,
    skill_registry: SkillRegistry,
    agent_defaults: AgentDefaults | None,
    session_context: SessionContext | None,
    workspace_path: Path | None,
    items: list[str] | None = None,
) -> CascadeResult | dict:
    """Run the cascade-aware activation loop.

    Returns an error dict on validation/routing failure, or a CascadeResult on success.
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
        # Gate 5: items required for loop steps
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
            return CascadeResult(
                outcome=CascadeOutcome(
                    deepest_layer_id=current.id,
                    active_step_id=step,
                    condition_skips=condition_skips,
                    finalized_top_level=False,
                ),
                mutations=batch,
                next_step_id=step,
                next_step_info=step_info,
                handoff_for_next=None,
                breadcrumb_parts=_build_breadcrumb_parts(layers, chain_order, current_steps),
                deepest_snapshot=current.definition_snapshot,
                scratchpad_path=scratchpad_path,
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

        next_step, next_info = _find_next_step_and_condition(ss, snapshot)

        # Condition halt: stop auto-advance and surface condition to agent
        if next_step is not None and has_condition_support:
            condition = next_info.get("condition") if next_info else None
            if condition:
                batch.ordered.append(
                    UpdateState(
                        layer_id=current.id,
                        step_states=dict(ss),
                        current_step=current_steps.get(current.id),
                    )
                )
                return CascadeResult(
                    outcome=CascadeOutcome(
                        deepest_layer_id=current.id,
                        active_step_id=next_step,
                        condition_skips=condition_skips,
                        finalized_top_level=False,
                        halted_at_condition_step=next_step,
                    ),
                    mutations=batch,
                    next_step_id=next_step,
                    next_step_info=next_info,
                    handoff_for_next=None,
                    breadcrumb_parts=_build_breadcrumb_parts(layers, chain_order, current_steps),
                    deepest_snapshot=current.definition_snapshot,
                    scratchpad_path=scratchpad_path,
                )

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
                return CascadeResult(
                    outcome=CascadeOutcome(
                        deepest_layer_id=current.id,
                        active_step_id=None,
                        condition_skips=condition_skips,
                        finalized_top_level=True,
                    ),
                    mutations=batch,
                    next_step_id=None,
                    next_step_info=None,
                    handoff_for_next=None,
                    breadcrumb_parts=[],
                    deepest_snapshot=current.definition_snapshot,
                    scratchpad_path=scratchpad_path,
                )

        assert next_info is not None
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
            return CascadeResult(
                outcome=CascadeOutcome(
                    deepest_layer_id=current.id,
                    active_step_id=next_step,
                    condition_skips=condition_skips,
                    finalized_top_level=False,
                    halted_at_loop_step=next_step,
                ),
                mutations=batch,
                next_step_id=next_step,
                next_step_info=next_info,
                handoff_for_next=None,
                breadcrumb_parts=_build_breadcrumb_parts(layers, chain_order, current_steps),
                deepest_snapshot=current.definition_snapshot,
                scratchpad_path=scratchpad_path,
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
        return CascadeResult(
            outcome=CascadeOutcome(
                deepest_layer_id=current.id,
                active_step_id=next_step,
                condition_skips=condition_skips,
                finalized_top_level=False,
            ),
            mutations=batch,
            next_step_id=next_step,
            next_step_info=next_info,
            handoff_for_next=None,
            breadcrumb_parts=_build_breadcrumb_parts(layers, chain_order, current_steps),
            deepest_snapshot=current.definition_snapshot,
            scratchpad_path=scratchpad_path,
        )
