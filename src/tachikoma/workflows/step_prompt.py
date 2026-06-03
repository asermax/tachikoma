"""Step prompt construction for workflow background task execution.

Provides the workflow step system prompt and a pure function for building
the full step prompt injected by the WorkflowStepContextProvider.

The prompt structure follows the contract in the DLT-176 design:
- Step metadata (workflow name, skill, position)
- Instructions from the step's instructions.md
- Resolved required skills content
- Scratchpad reference
- Hand-off from previous step (if present)
- Available workflow tools guidance
"""

from pathlib import Path

from loguru import logger

from tachikoma.skills.registry import SkillRegistry, render_skill_block

_log = logger.bind(component="workflow_step_prompt")

WORKFLOW_STEP_SYSTEM_PROMPT = """\
You are a workflow step agent. \
You are executing a single step of a multi-step workflow autonomously.

## Workflow Tools

You have access to the following workflow-specific tools:

- **complete_step(handoff="summary")**: Complete this step and advance to the next.
  The hand-off message (max 4000 chars) is relayed to the next step's agent.
  Use it to pass key context, decisions made, and pointers to scratchpad details.
  When all steps are complete, the workflow is auto-finalized.
- **skip_step()**: Skip this step (only if not required). Advances to the next step.
- **abort_workflow()**: Abort the entire workflow immediately.
- **request_input(question)**: Ask the user a question and wait for their response.
  Execution pauses until the user replies. Use this when you genuinely need human input.

## Guidelines

- Read the scratchpad at the start for accumulated context from previous steps.
- Write to the scratchpad to persist findings, decisions, and intermediate results.
- When done, call `complete_step(handoff="...")` with a concise summary of what you accomplished.
- If the step cannot be completed, call `abort_workflow()` with a reason.
- You are operating autonomously. Be thorough but efficient.
"""


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


def _resolve_required_skills(step_info: dict, registry: SkillRegistry) -> str:
    """Resolve declared skills for a step, returning XML-tagged content.

    Follows the same deps-first resolution as tools._render_required_skills.
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

    return "\n\n".join(ordered_blocks)


def build_step_prompt(
    *,
    step_info: dict,
    registry: SkillRegistry,
    scratchpad_path: str,
    workflow_id: str,
    skill_name: str,
    workflow_name: str,
    step_id: str,
    step_title: str,
    position: int,
    total_steps: int,
    handoff: str | None = None,
) -> str:
    """Build the full prompt for a workflow step task.

    Pure function — reads step instructions from disk but has no other
    side effects.

    Args:
        step_info: Step definition snapshot dict (has "path", "required_skills", etc.).
        registry: SkillRegistry for resolving required skill chains.
        scratchpad_path: Absolute path to the workflow's scratchpad file.
        workflow_id: The top-level workflow state ID.
        skill_name: Skill containing the workflow.
        workflow_name: Name of the workflow.
        step_id: The step's identifier.
        step_title: Human-readable step title.
        position: 1-indexed position within the current layer's step list.
        total_steps: Total steps in the current layer.
        handoff: Optional hand-off message from the previous step.

    Returns:
        Formatted step prompt string.
    """
    parts: list[str] = []

    # Header
    parts.append(
        f"## Workflow Step\n\n"
        f'You are executing step "{step_title}" (`{step_id}`) of workflow '
        f'"{workflow_name}" from skill "{skill_name}". '
        f"Step {position} of {total_steps}.\n\n"
        f"Workflow ID: `{workflow_id}`\n"
        f"Scratchpad: `{scratchpad_path}`"
    )

    # Instructions
    instructions = _read_step_instructions(step_info)
    if instructions:
        parts.append(f"### Instructions\n\n{instructions}")

    # Required skills
    skills_content = _resolve_required_skills(step_info, registry)
    if skills_content:
        parts.append(f"### Required Skills\n\n{skills_content}")

    # Hand-off
    if handoff:
        parts.append(f"### Hand-Off from Previous Step\n\n{handoff}")

    # Available tools guidance (always present)
    parts.append(
        "### Available Workflow Tools\n\n"
        '- `complete_step(handoff="summary")`: Complete this step and advance to the next. '
        "The hand-off message (max 4000 chars) is relayed to the next step's agent.\n"
        "- `skip_step()`: Skip this step (only if not required). Advances to next step.\n"
        "- `abort_workflow()`: Abort the entire workflow immediately.\n"
        "- `request_input(question)`: Ask the user a question and wait for response. "
        "Execution pauses until the user replies.\n\n"
        f"Read the scratchpad at `{scratchpad_path}` for accumulated context from previous steps."
    )

    return "\n\n".join(parts)
