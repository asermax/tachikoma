"""Tests for workflow definition model and loader.

Tests for DLT-081: Workflow State Machine for Skills - Batch 1.
"""

from pathlib import Path

import pytest

from tachikoma.skills.registry import SkillRegistry
from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition
from tachikoma.workflows.loader import load_workflows


class TestStepDefinition:
    """Tests for StepDefinition dataclass."""

    def test_construction_with_required_fields(self) -> None:
        """StepDefinition can be constructed with required fields."""
        step = StepDefinition(
            id="01-step",
            title="First Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        assert step.id == "01-step"
        assert step.title == "First Step"
        assert step.instructions_path == Path("/instructions.md")
        assert step.references_path is None
        assert step.scripts_path is None

    def test_default_required_is_true(self) -> None:
        """StepDefinition defaults required to True."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        assert step.required is True

    def test_required_can_be_set_false(self) -> None:
        """StepDefinition accepts required=False."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
            required=False,
        )

        assert step.required is False

    def test_default_properties_is_empty_dict(self) -> None:
        """StepDefinition defaults properties to empty dict."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        assert step.properties == {}

    def test_default_required_skills_is_empty_tuple(self) -> None:
        """StepDefinition defaults required_skills to empty tuple."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        assert step.required_skills == ()

    def test_required_skills_accepts_tuple_of_strings(self) -> None:
        """StepDefinition accepts required_skills as tuple of skill names."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
            required_skills=("skill-a", "skill-b"),
        )

        assert step.required_skills == ("skill-a", "skill-b")

    def test_properties_can_contain_extensible_fields(self) -> None:
        """StepDefinition accepts extensible properties."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
            properties={"custom_field": "value", "another": 123},
        )

        assert step.properties == {"custom_field": "value", "another": 123}

    def test_frozen_prevents_mutation(self) -> None:
        """StepDefinition is immutable."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        with pytest.raises(Exception):  # FrozenInstanceError from dataclasses
            step.title = "New Title"

    def test_references_path_can_be_set(self) -> None:
        """StepDefinition accepts references_path."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=Path("/references"),
            scripts_path=None,
        )

        assert step.references_path == Path("/references")

    def test_scripts_path_can_be_set(self) -> None:
        """StepDefinition accepts scripts_path."""
        step = StepDefinition(
            id="01-step",
            title="Step",
            instructions_path=Path("/instructions.md"),
            references_path=None,
            scripts_path=Path("/scripts"),
        )

        assert step.scripts_path == Path("/scripts")


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition dataclass."""

    def test_construction_with_required_fields(self) -> None:
        """WorkflowDefinition can be constructed with required fields."""
        workflow = WorkflowDefinition(
            skill_name="my-skill",
            workflow_name="my-workflow",
            steps=[],
            path=Path("/workflows/my-workflow"),
        )

        assert workflow.skill_name == "my-skill"
        assert workflow.workflow_name == "my-workflow"
        assert workflow.steps == []
        assert workflow.path == Path("/workflows/my-workflow")

    def test_can_contain_multiple_steps(self) -> None:
        """WorkflowDefinition can contain multiple steps."""
        step1 = StepDefinition(
            id="01-first",
            title="First",
            instructions_path=Path("/1/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        step2 = StepDefinition(
            id="02-second",
            title="Second",
            instructions_path=Path("/2/instructions.md"),
            references_path=None,
            scripts_path=None,
        )

        workflow = WorkflowDefinition(
            skill_name="skill",
            workflow_name="workflow",
            steps=[step1, step2],
            path=Path("/workflow"),
        )

        assert len(workflow.steps) == 2
        assert workflow.steps[0].id == "01-first"
        assert workflow.steps[1].id == "02-second"

    def test_frozen_prevents_mutation(self) -> None:
        """WorkflowDefinition is immutable."""
        workflow = WorkflowDefinition(
            skill_name="skill",
            workflow_name="workflow",
            steps=[],
            path=Path("/workflow"),
        )

        with pytest.raises(Exception):
            workflow.workflow_name = "new-workflow"


class TestLoadWorkflows:
    """Tests for load_workflows function."""

    def test_returns_empty_dict_when_workflows_directory_missing(self, tmp_path: Path) -> None:
        """Returns empty dict when workflows/ directory doesn't exist."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        result = load_workflows(skill_dir, "test-skill")

        assert result == {}

    def test_returns_empty_dict_when_workflows_is_file_not_directory(self, tmp_path: Path) -> None:
        """Returns empty dict when workflows/ is a file, not directory."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "workflows").write_text("not a directory")

        result = load_workflows(skill_dir, "test-skill")

        assert result == {}

    def test_discovers_workflow_subdirectories(self, tmp_path: Path) -> None:
        """Discovers workflow subdirectories within workflows/."""
        skill_dir = tmp_path / "test-skill"
        workflows_dir = skill_dir / "workflows"
        workflow1_dir = workflows_dir / "workflow-1"
        workflow2_dir = workflows_dir / "workflow-2"

        workflow1_dir.mkdir(parents=True)
        workflow2_dir.mkdir(parents=True)

        # Create minimal step structure
        step1_dir = workflow1_dir / "01-step"
        step1_dir.mkdir()
        (step1_dir / "instructions.md").write_text("---\ntitle: Step One\n---\nInstructions.")

        step2_dir = workflow2_dir / "01-step"
        step2_dir.mkdir()
        (step2_dir / "instructions.md").write_text("---\ntitle: Step Two\n---\nInstructions.")

        result = load_workflows(skill_dir, "test-skill")

        assert len(result) == 2
        assert "workflow-1" in result
        assert "workflow-2" in result

    def test_steps_sorted_alphabetically(self, tmp_path: Path) -> None:
        """Steps are sorted alphabetically by directory name (R17)."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"

        # Create steps in non-alphabetical order
        step3 = workflow_dir / "03-review"
        step1 = workflow_dir / "01-plan"
        step2 = workflow_dir / "02-execute"

        for step_dir in [step3, step1, step2]:
            step_dir.mkdir(parents=True)
            (step_dir / "instructions.md").write_text(
                f"---\ntitle: {step_dir.name}\n---\nInstructions."
            )

        result = load_workflows(skill_dir, "test-skill")

        workflow = result["test-workflow"]
        assert [s.id for s in workflow.steps] == ["01-plan", "02-execute", "03-review"]

    def test_parses_frontmatter_title(self, tmp_path: Path) -> None:
        """Parses title from instructions.md frontmatter."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: My Custom Title
---
Instructions here."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.title == "My Custom Title"

    def test_parses_frontmatter_required(self, tmp_path: Path) -> None:
        """Parses required from instructions.md frontmatter."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required: false
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required is False

    def test_default_required_when_not_in_frontmatter(self, tmp_path: Path) -> None:
        """Defaults required to True when not in frontmatter."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required is True

    def test_skippable_true_alias_sets_required_false(self, tmp_path: Path) -> None:
        """skippable: true in frontmatter is treated as required=false."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
skippable: true
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required is False

    def test_skippable_false_with_no_required_keeps_default(self, tmp_path: Path) -> None:
        """skippable: false with no required field keeps required=True default."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
skippable: false
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required is True

    def test_required_takes_precedence_over_skippable(self, tmp_path: Path) -> None:
        """When both required and skippable are present, required wins."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required: true
skippable: true
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required is True

    def test_required_and_skippable_excluded_from_properties(self, tmp_path: Path) -> None:
        """Neither required nor skippable appear in properties dict."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required: false
skippable: true
custom_field: keep-me
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert "required" not in step.properties
        assert "skippable" not in step.properties
        assert step.properties == {"custom_field": "keep-me"}

    def test_parses_required_skills(self, tmp_path: Path) -> None:
        """Parses required_skills from frontmatter into a tuple of strings."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required_skills:
  - skill-a
  - skill-b
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required_skills == ("skill-a", "skill-b")

    def test_default_required_skills_when_missing(self, tmp_path: Path) -> None:
        """Defaults required_skills to empty tuple when absent from frontmatter."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required_skills == ()

    def test_malformed_required_skills_falls_back_to_empty(self, tmp_path: Path) -> None:
        """Malformed required_skills produces a warning and falls back to empty tuple."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        # required_skills as a string instead of a list
        (step_dir / "instructions.md").write_text(
            """---
title: Step
required_skills: not-a-list
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required_skills == ()
        assert step.title == "Step"

    def test_malformed_required_skills_non_string_entries(self, tmp_path: Path) -> None:
        """required_skills with non-string entries falls back to empty tuple."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required_skills:
  - 123
  - skill-b
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.required_skills == ()

    def test_required_skills_excluded_from_properties(self, tmp_path: Path) -> None:
        """required_skills field is not passed through as an extensible property."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required_skills:
  - skill-a
custom_field: keep-me
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert "required_skills" not in step.properties
        assert step.properties == {"custom_field": "keep-me"}

    def test_parses_extensible_frontmatter_fields(self, tmp_path: Path) -> None:
        """Parses extensible fields from frontmatter into properties."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
custom_field: custom_value
number: 42
list:
  - one
  - two
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.properties == {
            "custom_field": "custom_value",
            "number": 42,
            "list": ["one", "two"],
        }

    def test_step_without_instructions_md_is_skipped(self, tmp_path: Path) -> None:
        """Step without instructions.md is skipped with warning."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)
        # Don't create instructions.md

        result = load_workflows(skill_dir, "test-skill")

        workflow = result["test-workflow"]
        assert len(workflow.steps) == 0

        # Note: Loguru writes to stderr (not stdlib logging), so caplog cannot capture
        # the warning message. The warning is visible in pytest's captured stderr output.

    def test_invalid_frontmatter_title_skips_step(self, tmp_path: Path) -> None:
        """Step with invalid frontmatter title is skipped with warning."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: 123
---
Instructions."""  # title should be string, not int
        )

        result = load_workflows(skill_dir, "test-skill")

        workflow = result["test-workflow"]
        assert len(workflow.steps) == 0

        # Note: Loguru writes to stderr (not stdlib logging), so caplog cannot capture
        # the warning message. The warning is visible in pytest's captured stderr output.

    def test_invalid_frontmatter_required_skips_step(self, tmp_path: Path) -> None:
        """Step with invalid required type is skipped with warning."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
required: "not-a-bool"
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        workflow = result["test-workflow"]
        assert len(workflow.steps) == 0

        # Note: Loguru writes to stderr (not stdlib logging), so caplog cannot capture
        # the warning message. The warning is visible in pytest's captured stderr output.

    def test_workflow_with_no_valid_steps_included(self, tmp_path: Path) -> None:
        """Workflow with no valid steps is included with empty steps list."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)
        # Don't create instructions.md - step will be skipped

        result = load_workflows(skill_dir, "test-skill")

        assert "test-workflow" in result
        assert result["test-workflow"].steps == []

    def test_references_directory_when_exists(self, tmp_path: Path) -> None:
        """Step with references/ subdirectory has references_path set."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        refs_dir = step_dir / "references"
        refs_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.references_path == refs_dir

    def test_references_directory_when_missing(self, tmp_path: Path) -> None:
        """Step without references/ subdirectory has references_path as None."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.references_path is None

    def test_references_directory_when_is_file(self, tmp_path: Path) -> None:
        """Step with references/ as a file has references_path as None."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        # Create references as a file, not directory
        (step_dir / "references").write_text("not a directory")

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.references_path is None

    def test_scripts_directory_when_exists(self, tmp_path: Path) -> None:
        """Step with scripts/ subdirectory has scripts_path set."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        scripts_dir = step_dir / "scripts"
        scripts_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.scripts_path == scripts_dir

    def test_scripts_directory_when_missing(self, tmp_path: Path) -> None:
        """Step without scripts/ subdirectory has scripts_path as None."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.scripts_path is None

    def test_both_optional_directories_exist(self, tmp_path: Path) -> None:
        """Step can have both references/ and scripts/ directories."""
        skill_dir = tmp_path / "test-skill"
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        step_dir = workflow_dir / "01-step"
        refs_dir = step_dir / "references"
        scripts_dir = step_dir / "scripts"
        refs_dir.mkdir(parents=True)
        scripts_dir.mkdir(parents=True)

        (step_dir / "instructions.md").write_text(
            """---
title: Step
---
Instructions."""
        )

        result = load_workflows(skill_dir, "test-skill")

        step = result["test-workflow"].steps[0]
        assert step.references_path == refs_dir
        assert step.scripts_path == scripts_dir


class TestSkillRegistryIntegration:
    """Integration tests for SkillRegistry with workflow loading."""

    def test_workflow_discovery_within_skills(self, tmp_path: Path) -> None:
        """SkillRegistry discovers workflows within skills."""
        # Create skill directory structure
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        # Create SKILL.md
        (skill_dir / "SKILL.md").write_text(
            """---
description: Test skill
---
Skill body."""
        )

        # Create workflow
        workflow_dir = skill_dir / "workflows" / "test-workflow"
        workflow_dir.mkdir(parents=True)

        step_dir = workflow_dir / "01-step"
        step_dir.mkdir()
        (step_dir / "instructions.md").write_text(
            """---
title: Test Step
---
Instructions."""
        )

        # Create registry
        registry = SkillRegistry([tmp_path])

        # Check workflow was discovered
        workflow = registry.get_workflow("test-skill", "test-workflow")
        assert workflow is not None
        assert workflow.skill_name == "test-skill"
        assert workflow.workflow_name == "test-workflow"
        assert len(workflow.steps) == 1
        assert workflow.steps[0].title == "Test Step"

    def test_workflows_property_returns_all_workflows(self, tmp_path: Path) -> None:
        """SkillRegistry.workflows property returns all discovered workflows."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        (skill_dir / "SKILL.md").write_text(
            """---
description: Test skill
---
Body."""
        )

        # Create multiple workflows
        for wf_name in ["workflow-1", "workflow-2"]:
            workflow_dir = skill_dir / "workflows" / wf_name
            workflow_dir.mkdir(parents=True)

            step_dir = workflow_dir / "01-step"
            step_dir.mkdir()
            (step_dir / "instructions.md").write_text(
                f"""---
title: {wf_name} step
---
Instructions."""
            )

        registry = SkillRegistry([tmp_path])

        workflows = registry.workflows
        assert len(workflows) == 2
        assert ("test-skill", "workflow-1") in workflows
        assert ("test-skill", "workflow-2") in workflows

    def test_skill_replacement_clears_workflows(self, tmp_path: Path) -> None:
        """Skill replacement clears previous workflow entries."""
        # Create first source with workflow
        source1 = tmp_path / "source1"
        skill1_dir = source1 / "test-skill"
        skill1_dir.mkdir(parents=True)

        (skill1_dir / "SKILL.md").write_text(
            """---
description: Skill from source1
---
Body."""
        )

        wf1_dir = skill1_dir / "workflows" / "workflow-1"
        wf1_dir.mkdir(parents=True)
        step1_dir = wf1_dir / "01-step"
        step1_dir.mkdir()
        (step1_dir / "instructions.md").write_text(
            """---
title: Source1 Step
---
Instructions."""
        )

        # Create second source with same skill but different workflow
        source2 = tmp_path / "source2"
        skill2_dir = source2 / "test-skill"
        skill2_dir.mkdir(parents=True)

        (skill2_dir / "SKILL.md").write_text(
            """---
description: Skill from source2
---
Body."""
        )

        wf2_dir = skill2_dir / "workflows" / "workflow-2"
        wf2_dir.mkdir(parents=True)
        step2_dir = wf2_dir / "01-step"
        step2_dir.mkdir()
        (step2_dir / "instructions.md").write_text(
            """---
title: Source2 Step
---
Instructions."""
        )

        # Create registry with both sources (last-wins)
        registry = SkillRegistry([source1, source2])

        # Check that workflow-1 was replaced by workflow-2
        assert registry.get_workflow("test-skill", "workflow-1") is None
        assert registry.get_workflow("test-skill", "workflow-2") is not None

        workflows = registry.workflows
        assert ("test-skill", "workflow-1") not in workflows
        assert ("test-skill", "workflow-2") in workflows

    def test_get_workflow_returns_none_for_unknown(self, tmp_path: Path) -> None:
        """get_workflow returns None for unknown skill/workflow."""
        registry = SkillRegistry([tmp_path])

        assert registry.get_workflow("unknown", "unknown") is None
        assert registry.get_workflow("test-skill", "unknown") is None

    def test_refresh_rebuilds_workflows_dict(self, tmp_path: Path) -> None:
        """refresh() rebuilds workflows dict alongside skills dict."""
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()

        (skill_dir / "SKILL.md").write_text(
            """---
description: Test skill
---
Body."""
        )

        # Initial state - no workflows
        registry = SkillRegistry([tmp_path])
        assert len(registry.workflows) == 0

        # Add workflow
        workflow_dir = skill_dir / "workflows" / "new-workflow"
        workflow_dir.mkdir(parents=True)
        step_dir = workflow_dir / "01-step"
        step_dir.mkdir()
        (step_dir / "instructions.md").write_text(
            """---
title: New Step
---
Instructions."""
        )

        # Mark dirty and refresh
        registry.mark_dirty()
        registry.refresh()

        # Workflow should now be discovered
        assert registry.get_workflow("test-skill", "new-workflow") is not None
        assert len(registry.workflows) == 1
