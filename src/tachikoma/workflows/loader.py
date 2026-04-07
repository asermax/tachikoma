"""Workflow definition loader for discovering workflows from skill directories.

This module provides functionality to discover and parse workflow definitions
from the filesystem. Workflows are discovered as subdirectories within skill's
workflows/ directories, with steps parsed as subdirectories containing
instructions.md files with YAML frontmatter.
"""

from pathlib import Path

import frontmatter
from loguru import logger

from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition

_log = logger.bind(component="workflows")


def load_workflows(skill_dir: Path, skill_name: str) -> dict[str, WorkflowDefinition]:
    """Discover and load workflow definitions from a skill directory.

    Scans the workflows/ subdirectory within skill_dir for workflow definitions.
    Each workflow is a subdirectory containing step subdirectories sorted
    alphabetically. Steps are parsed from instructions.md files with YAML frontmatter.

    Args:
        skill_dir: Path to the skill directory.
        skill_name: Name of the skill (used in workflow definitions).

    Returns:
        Dictionary mapping workflow name to WorkflowDefinition.
    """
    workflows_dir = skill_dir / "workflows"

    if not workflows_dir.exists() or not workflows_dir.is_dir():
        _log.debug(
            "No workflows directory found: skill={skill}, path={path}",
            skill=skill_name,
            path=str(workflows_dir),
        )
        return {}

    workflows: dict[str, WorkflowDefinition] = {}

    try:
        items = list(workflows_dir.iterdir())
    except Exception as exc:
        _log.warning(
            "Failed to list workflows directory: skill={skill}, path={path}, err={err}",
            skill=skill_name,
            path=str(workflows_dir),
            err=str(exc),
        )
        return {}

    for item in items:
        if not item.is_dir():
            continue

        workflow_name = item.name
        workflow_def = _load_workflow(item, skill_name, workflow_name)

        if workflow_def is not None:
            workflows[workflow_name] = workflow_def

    return workflows


def _load_workflow(
    workflow_dir: Path,
    skill_name: str,
    workflow_name: str,
) -> WorkflowDefinition | None:
    """Load a single workflow definition from its directory.

    Args:
        workflow_dir: Path to the workflow directory.
        skill_name: Name of the parent skill.
        workflow_name: Name of the workflow.

    Returns:
        WorkflowDefinition if at least one valid step found, None otherwise.
    """
    try:
        items = list(workflow_dir.iterdir())
    except Exception as exc:
        _log.warning(
            "Failed to list workflow directory: skill={skill}, workflow={workflow}, err={err}",
            skill=skill_name,
            workflow=workflow_name,
            err=str(exc),
        )
        return None

    step_dirs = sorted(
        [item for item in items if item.is_dir()],
        key=lambda p: p.name,
    )

    steps: list[StepDefinition] = []

    for step_dir in step_dirs:
        step_def = _load_step(step_dir, skill_name, workflow_name)

        if step_def is not None:
            steps.append(step_def)

    workflow_def = WorkflowDefinition(
        skill_name=skill_name,
        workflow_name=workflow_name,
        steps=steps,
        path=workflow_dir,
    )

    _log.debug(
        "Loaded workflow: skill={skill}, workflow={workflow}, steps={count}",
        skill=skill_name,
        workflow=workflow_name,
        count=len(steps),
    )

    return workflow_def


def _load_step(
    step_dir: Path,
    skill_name: str,
    workflow_name: str,
) -> StepDefinition | None:
    """Load a single step definition from its directory.

    Args:
        step_dir: Path to the step directory.
        skill_name: Name of the parent skill.
        workflow_name: Name of the parent workflow.

    Returns:
        StepDefinition if valid, None if instructions.md missing or invalid.
    """
    instructions_path = step_dir / "instructions.md"

    if not instructions_path.exists():
        _log.warning(
            "Step missing instructions.md: skill={skill}, workflow={workflow}, step={step}",
            skill=skill_name,
            workflow=workflow_name,
            step=step_dir.name,
        )
        return None

    try:
        post = frontmatter.load(str(instructions_path))
    except Exception as exc:
        _log.warning(
            "Failed to parse instructions.md: skill={skill}, workflow={workflow}, "
            "step={step}, err={err}",
            skill=skill_name,
            workflow=workflow_name,
            step=step_dir.name,
            err=str(exc),
        )
        return None

    title = post.metadata.get("title", "")

    if not title or not isinstance(title, str):
        _log.warning(
            "Step has missing or invalid title: skill={skill}, workflow={workflow}, step={step}",
            skill=skill_name,
            workflow=workflow_name,
            step=step_dir.name,
        )
        return None

    skippable = post.metadata.get("skippable", False)

    if not isinstance(skippable, bool):
        _log.warning(
            "Step has invalid skippable type: skill={skill}, workflow={workflow}, step={step}",
            skill=skill_name,
            workflow=workflow_name,
            step=step_dir.name,
        )
        return None

    properties = {k: v for k, v in post.metadata.items() if k not in ("title", "skippable")}

    references_path = step_dir / "references"
    if not references_path.exists() or not references_path.is_dir():
        references_path = None

    scripts_path = step_dir / "scripts"
    if not scripts_path.exists() or not scripts_path.is_dir():
        scripts_path = None

    step_def = StepDefinition(
        id=step_dir.name,
        title=title,
        instructions_path=instructions_path,
        references_path=references_path,
        scripts_path=scripts_path,
        skippable=skippable,
        properties=properties,
    )

    _log.debug(
        "Loaded step: skill={skill}, workflow={workflow}, step={step}, title={title}",
        skill=skill_name,
        workflow=workflow_name,
        step=step_dir.name,
        title=title,
    )

    return step_def
