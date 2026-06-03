"""Workflow step context provider for background task pre-processing.

Constructs the full step prompt (instructions, skills, scratchpad, metadata,
hand-off) for workflow task instances. The executor registers this provider
in the pre-processing pipeline when the TaskInstance has a workflow_id set.

The provider reads workflow state from the database, finds the current step,
resolves required skills, reads the scratchpad path, consumes and clears any
pending hand-off, and delegates to build_step_prompt() for prompt assembly.
"""

from loguru import logger

from tachikoma.pre_processing import ContextProvider, ContextResult
from tachikoma.skills.registry import SkillRegistry
from tachikoma.tasks.model import TaskInstance
from tachikoma.workflows.cascade import _find_next_step_and_condition
from tachikoma.workflows.model import STEP_STARTED, WorkflowState
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.step_prompt import build_step_prompt

_log = logger.bind(component="workflow_step_context")


class WorkflowStepContextProvider(ContextProvider):
    """Pre-processing context provider for workflow step tasks.

    Reads workflow state, resolves the current step, builds the step prompt
    with instructions, skills, scratchpad reference, hand-off, and metadata.

    Constructor receives the TaskInstance and dependencies — the executor
    creates this provider when workflow_id is set on the instance.
    """

    def __init__(
        self,
        instance: TaskInstance,
        repository: WorkflowStateRepository,
        skill_registry: SkillRegistry,
    ) -> None:
        self._instance = instance
        self._repository = repository
        self._skill_registry = skill_registry

    def status_message(self, result: "ContextResult | None" = None) -> str:
        if result is None:
            return "Loading workflow step context..."
        return "Workflow step context loaded"

    async def provide(self, message: str) -> ContextResult | None:
        """Build and return the workflow step context.

        Steps:
        1. Read workflow state chain via repository
        2. Find deepest active layer and current step
        3. Read pending handoff, then clear it
        4. Resolve step info and required skills
        5. Build step prompt via build_step_prompt()
        6. Return ContextResult with tag="workflow-step"
        """
        workflow_id = self._instance.workflow_id
        if workflow_id is None:
            _log.warning("WorkflowStepContextProvider called with no workflow_id")
            return None

        chain = await self._repository.get_active_chain(workflow_id)
        if not chain:
            _log.warning(
                "No active workflow chain found: workflow_id={workflow_id}",
                workflow_id=workflow_id,
            )
            return None

        # The deepest layer is the active one
        deepest = chain[-1]
        top_level = chain[0]

        # Find the current step — either the started step or the next pending one
        step_info = _resolve_current_step(deepest)
        if step_info is None:
            _log.warning(
                "No active or pending step found: workflow_id={workflow_id}",
                workflow_id=workflow_id,
            )
            return None

        step_id = step_info["id"]
        step_title = step_info.get("title", step_id)

        # Read and consume pending handoff
        handoff = top_level.pending_handoff
        if handoff is not None:
            await self._repository.update_pending_handoff(workflow_id, None)

        # Compute step position within the current layer
        position, total_steps = _compute_step_position(deepest.definition_snapshot, step_id)

        prompt = build_step_prompt(
            step_info=step_info,
            registry=self._skill_registry,
            scratchpad_path=top_level.scratchpad_path,
            workflow_id=workflow_id,
            skill_name=top_level.skill_name,
            workflow_name=top_level.workflow_name,
            step_id=step_id,
            step_title=step_title,
            position=position,
            total_steps=total_steps,
            handoff=handoff,
        )

        return ContextResult(tag="workflow-step", content=prompt)


def _resolve_current_step(state: WorkflowState) -> dict | None:
    """Find the current step from a workflow state.

    Prefer the started step; fall back to the next pending step.
    """
    # If there's a started step, find its info
    if state.current_step:
        for step_def in state.definition_snapshot:
            if step_def["id"] == state.current_step:
                step_state = state.step_states.get(step_def["id"])
                if step_state == STEP_STARTED:
                    return step_def

    # Fall back to the next pending step
    step_id, step_info = _find_next_step_and_condition(
        state.step_states, state.definition_snapshot
    )
    if step_id is not None:
        return step_info

    # Last resort: try to match the instance prompt to a step ID
    return None


def _compute_step_position(
    definition_snapshot: list[dict], step_id: str
) -> tuple[int, int]:
    """Compute 1-indexed position and total steps for a step within its layer."""
    total = len(definition_snapshot)
    for i, step_def in enumerate(definition_snapshot, 1):
        if step_def["id"] == step_id:
            return i, total
    return 0, total
