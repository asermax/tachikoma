"""Workflow definition dataclasses for filesystem-parsed workflow model.

This module defines the frozen dataclasses that represent workflow definitions
discovered from the filesystem. Workflows are directory-based structures within
skills containing ordered steps with instructions, references, and scripts.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class StepDefinition:
    """Definition for a single workflow step parsed from filesystem.

    A step represents a single stage in a workflow, discovered from a subdirectory
    within a workflow directory. Each step has an instructions.md file with YAML
    frontmatter containing metadata, and optional references/ and scripts/ subdirectories.

    Attributes:
        id: Step identifier (matches the directory name).
        title: Human-readable title from frontmatter.
        instructions_path: Path to the instructions.md file.
        references_path: Path to references/ directory if exists, None otherwise.
        scripts_path: Path to scripts/ directory if exists, None otherwise.
        required: Whether this step must execute (default True).
            When False, the step can be skipped.
        required_skills: Declared skills that must be loaded when this step is activated.
            The workflow engine resolves the transitive dependency chain via the
            skill registry and injects the resolved skills into the step's tool
            response, bypassing classification.
        condition: Natural language prompt evaluated before step start.
            If the evaluator determines the condition is not met, the step
            is auto-skipped regardless of ``required`` status.
        composes: Raw frontmatter value referencing another workflow to compose.
            ``<workflow>`` for same-skill or ``<skill>/<workflow>`` for cross-skill.
            Resolved at registry validation time via composition.resolve_composes().
        properties: Extensible frontmatter fields for future customization.
    """

    id: str
    title: str
    instructions_path: Path
    references_path: Path | None
    scripts_path: Path | None
    required: bool = True
    required_skills: tuple[str, ...] = ()
    condition: str | None = None
    composes: str | None = None
    properties: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowDefinition:
    """Definition for a workflow parsed from filesystem.

    A workflow is an ordered sequence of steps within a skill, discovered from a
    workflows/{workflow_name}/ directory structure. Steps are ordered alphabetically
    by their directory names (e.g., 01-plan, 02-execute, 03-review).

    Attributes:
        skill_name: Name of the parent skill.
        workflow_name: Name of the workflow (matches directory name).
        steps: Ordered list of step definitions.
        path: Path to the workflow directory.
    """

    skill_name: str
    workflow_name: str
    steps: list[StepDefinition]
    path: Path
