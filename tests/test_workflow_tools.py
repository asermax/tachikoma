"""Tests for workflow MCP tools, handlers, and transition validation."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tachikoma.agent_defaults import AgentDefaults
from tachikoma.database import Base
from tachikoma.session_context import SessionContext
from tachikoma.skills.registry import Skill, SkillRegistry
from tachikoma.workflows.conditions import ConditionResult
from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition
from tachikoma.workflows.model import WorkflowState
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import (
    EndWorkflowArgs,
    GetWorkflowStateArgs,
    ListActiveWorkflowsArgs,
    StartWorkflowArgs,
    UpdateWorkflowStateArgs,
    _evaluate_and_advance,
    _find_next_pending_step,
    _render_breadcrumb,
    _render_required_skills,
    _step_to_snapshot,
    handle_end_workflow,
    handle_get_workflow_state,
    handle_list_active_workflows,
    handle_start_workflow,
    handle_update_workflow_state,
    validate_transition,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_factory():
    """Create an in-memory SQLite session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def repository(session_factory):
    """Create a WorkflowStateRepository for testing."""
    return WorkflowStateRepository(session_factory)


def _make_step(
    step_id: str,
    title: str,
    required: bool = True,
    condition: str | None = None,
) -> StepDefinition:
    """Create a test StepDefinition."""
    return StepDefinition(
        id=step_id,
        title=title,
        instructions_path=Path(f"/fake/skill/workflows/test/{step_id}/instructions.md"),
        references_path=None,
        scripts_path=None,
        required=required,
        condition=condition,
    )


def _make_workflow(
    steps: list[StepDefinition] | None = None,
    skill_name: str = "test-skill",
    workflow_name: str = "test-workflow",
) -> WorkflowDefinition:
    """Create a test WorkflowDefinition."""
    if steps is None:
        steps = [
            _make_step("01-plan", "Plan"),
            _make_step("02-execute", "Execute"),
            _make_step("03-review", "Review"),
        ]

    return WorkflowDefinition(
        skill_name=skill_name,
        workflow_name=workflow_name,
        steps=steps,
        path=Path(f"/fake/skills/{skill_name}/workflows/{workflow_name}"),
    )


def _make_state(
    workflow_id: str = "test-wf-id",
    skill_name: str = "test-skill",
    workflow_name: str = "test-workflow",
    step_states: dict | None = None,
    definition_snapshot: list[dict] | None = None,
    scratchpad_path: str | None = None,
) -> WorkflowState:
    """Create a test WorkflowState."""
    if definition_snapshot is None:
        definition_snapshot = [
            {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
            },
            {"id": "03-review", "title": "Review", "required": False, "path": "/fake/03-review"},
        ]

    if step_states is None:
        step_states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}

    if scratchpad_path is None:
        scratchpad_path = f"/tmp/scratchpad-workflow-{workflow_id}.md"

    now = datetime.now(UTC)

    return WorkflowState(
        id=workflow_id,
        skill_name=skill_name,
        workflow_name=workflow_name,
        current_step=None,
        step_states=step_states,
        definition_snapshot=definition_snapshot,
        scratchpad_path=scratchpad_path,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_registry():
    """Create a mock SkillRegistry with a test workflow."""
    registry = MagicMock(spec=SkillRegistry)
    workflow = _make_workflow()
    registry.get_workflow.return_value = workflow
    registry.workflows = {("test-skill", "test-workflow"): workflow}
    return registry


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_start_workflow_args_valid(self):
        parsed = StartWorkflowArgs.model_validate(
            {
                "skill_name": "my-skill",
                "workflow_name": "my-workflow",
            }
        )
        assert parsed.skill_name == "my-skill"
        assert parsed.workflow_name == "my-workflow"

    def test_start_workflow_args_missing_fields(self):
        with pytest.raises(Exception):
            StartWorkflowArgs.model_validate({})

    def test_update_workflow_state_args_valid(self):
        parsed = UpdateWorkflowStateArgs.model_validate(
            {
                "workflow_id": "abc",
                "step": "01-plan",
                "action": "start",
            }
        )
        assert parsed.action == "start"

    def test_update_workflow_state_args_invalid_action(self):
        with pytest.raises(Exception):
            UpdateWorkflowStateArgs.model_validate(
                {
                    "workflow_id": "abc",
                    "step": "01-plan",
                    "action": "invalid",
                }
            )

    def test_get_workflow_state_args_valid(self):
        parsed = GetWorkflowStateArgs.model_validate({"workflow_id": "abc"})
        assert parsed.workflow_id == "abc"

    def test_end_workflow_args_valid(self):
        parsed = EndWorkflowArgs.model_validate(
            {
                "workflow_id": "abc",
                "action": "complete",
            }
        )
        assert parsed.action == "complete"

    def test_end_workflow_args_invalid_action(self):
        with pytest.raises(Exception):
            EndWorkflowArgs.model_validate(
                {
                    "workflow_id": "abc",
                    "action": "invalid",
                }
            )

    def test_list_active_workflows_args(self):
        parsed = ListActiveWorkflowsArgs.model_validate({})
        assert parsed is not None


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------


class TestTransitionValidation:
    SNAPSHOT = [
        {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
        {"id": "02-execute", "title": "Execute", "required": True, "path": "/fake/02-execute"},
        {"id": "03-review", "title": "Review", "required": False, "path": "/fake/03-review"},
    ]

    def test_start_pending_step_valid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        assert validate_transition(states, "01-plan", "start", self.SNAPSHOT) is None

    def test_start_started_step_invalid(self):
        states = {"01-plan": "started", "02-execute": "pending", "03-review": "pending"}
        error = validate_transition(states, "01-plan", "start", self.SNAPSHOT)
        assert error is not None
        assert "already started" in error

    def test_complete_started_step_valid(self):
        states = {"01-plan": "started", "02-execute": "pending", "03-review": "pending"}
        assert validate_transition(states, "01-plan", "complete", self.SNAPSHOT) is None

    def test_complete_pending_step_invalid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        error = validate_transition(states, "01-plan", "complete", self.SNAPSHOT)
        assert error is not None
        assert "Must start" in error

    def test_skip_non_required_pending_step_valid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        assert validate_transition(states, "03-review", "skip", self.SNAPSHOT) is None

    def test_skip_required_step_invalid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        error = validate_transition(states, "01-plan", "skip", self.SNAPSHOT)
        assert error is not None
        assert "required and cannot be skipped" in error

    def test_skip_started_step_invalid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "started"}
        error = validate_transition(states, "03-review", "skip", self.SNAPSHOT)
        assert error is not None
        assert "Can only skip a pending step" in error

    def test_completed_step_rejects_all_actions(self):
        states = {"01-plan": "completed", "02-execute": "pending", "03-review": "pending"}
        for action in ("start", "complete", "skip"):
            error = validate_transition(states, "01-plan", action, self.SNAPSHOT)
            assert error is not None, f"Action {action} should be rejected for completed step"
            assert "already completed" in error

    def test_skipped_step_rejects_all_actions(self):
        states = {"01-plan": "skipped", "02-execute": "pending", "03-review": "pending"}
        error = validate_transition(states, "01-plan", "start", self.SNAPSHOT)
        assert error is not None
        assert "already skipped" in error

    def test_invalid_step_id(self):
        states = {"01-plan": "pending"}
        error = validate_transition(states, "99-nonexistent", "start", self.SNAPSHOT)
        assert error is not None
        assert "Invalid step" in error
        assert "01-plan" in error  # Lists valid steps


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_step_to_snapshot(self):
        step = _make_step("01-plan", "Plan", required=False)
        snapshot = _step_to_snapshot(step)

        assert snapshot["id"] == "01-plan"
        assert snapshot["title"] == "Plan"
        assert snapshot["required"] is False
        assert snapshot["path"] == str(step.instructions_path.parent)
        assert snapshot["required_skills"] == []
        assert snapshot["condition"] is None

    def test_step_to_snapshot_with_condition(self):
        step = _make_step("01-plan", "Plan", condition="Only run if output.md exists")
        snapshot = _step_to_snapshot(step)

        assert snapshot["condition"] == "Only run if output.md exists"

    def test_step_to_snapshot_without_condition_is_none(self):
        step = _make_step("01-plan", "Plan")
        snapshot = _step_to_snapshot(step)

        assert snapshot["condition"] is None

    def test_step_to_snapshot_with_required_skills(self):
        step = StepDefinition(
            id="01-plan",
            title="Plan",
            instructions_path=Path("/fake/01-plan/instructions.md"),
            references_path=None,
            scripts_path=None,
            required_skills=("skill-a", "skill-b"),
        )

        snapshot = _step_to_snapshot(step)

        assert snapshot["required_skills"] == ["skill-a", "skill-b"]

    def test_find_next_pending_step(self):
        snapshot = [
            {"id": "01-plan", "title": "Plan"},
            {"id": "02-execute", "title": "Execute"},
            {"id": "03-review", "title": "Review"},
        ]
        states = {"01-plan": "completed", "02-execute": "started", "03-review": "pending"}
        assert _find_next_pending_step(states, snapshot) == "03-review"

    def test_find_next_pending_step_none(self):
        snapshot = [{"id": "01-plan", "title": "Plan"}]
        states = {"01-plan": "completed"}
        assert _find_next_pending_step(states, snapshot) is None


# ---------------------------------------------------------------------------
# Required skills rendering
# ---------------------------------------------------------------------------


def _make_skill(name: str, body: str = "body") -> Skill:
    return Skill(
        name=name,
        description=f"{name} description",
        body=body,
        path=Path(f"/fake/skills/{name}"),
    )


def _registry_with_chains(chains: dict[str, list[Skill]]) -> MagicMock:
    """Build a mock SkillRegistry whose resolve_chain returns the given chains."""
    registry = MagicMock(spec=SkillRegistry)

    def resolve(name: str) -> list[Skill]:
        if name not in chains:
            raise KeyError(name)
        return chains[name]

    registry.resolve_chain.side_effect = resolve
    return registry


class TestRenderRequiredSkills:
    def test_no_required_skills_returns_empty(self):
        step_info = {"id": "01-plan", "title": "Plan", "path": "/fake/01-plan"}
        registry = _registry_with_chains({})

        assert _render_required_skills(step_info, registry) == ""

    def test_missing_field_returns_empty(self):
        step_info = {"id": "01-plan", "required_skills": []}
        registry = _registry_with_chains({})

        assert _render_required_skills(step_info, registry) == ""

    def test_unknown_anchor_silently_skipped(self):
        step_info = {"id": "01-plan", "required_skills": ["ghost"]}
        registry = _registry_with_chains({})

        # No known skills resolve — helper returns empty (no empty header)
        assert _render_required_skills(step_info, registry) == ""

    def test_deps_first_ordering(self):
        skill_b = _make_skill("skill-b", "body-b")
        skill_a = _make_skill("skill-a", "body-a")
        # skill-a depends on skill-b, so chain is [b, a]
        step_info = {"required_skills": ["skill-a"]}
        registry = _registry_with_chains({"skill-a": [skill_b, skill_a]})

        rendered = _render_required_skills(step_info, registry)

        assert "## Required Skills" in rendered
        assert rendered.index("skill-b") < rendered.index("skill-a")
        assert "body-b" in rendered
        assert "body-a" in rendered

    def test_shared_transitive_dep_emitted_once(self):
        skill_d = _make_skill("skill-d", "body-d")
        skill_b = _make_skill("skill-b", "body-b")
        skill_c = _make_skill("skill-c", "body-c")
        # Both b and c depend on d; step declares [b, c]
        step_info = {"required_skills": ["skill-b", "skill-c"]}
        registry = _registry_with_chains(
            {
                "skill-b": [skill_d, skill_b],
                "skill-c": [skill_d, skill_c],
            }
        )

        rendered = _render_required_skills(step_info, registry)

        # skill-d body appears exactly once across the whole block
        assert rendered.count("body-d") == 1
        assert "body-b" in rendered
        assert "body-c" in rendered

    def test_mixed_known_and_unknown_renders_known(self):
        skill_a = _make_skill("skill-a", "body-a")
        step_info = {"required_skills": ["ghost", "skill-a"]}
        registry = _registry_with_chains({"skill-a": [skill_a]})

        rendered = _render_required_skills(step_info, registry)

        assert "body-a" in rendered
        assert "ghost" not in rendered

    def test_renders_xml_block_with_name_and_directory(self):
        skill_a = _make_skill("skill-a", "body-a")
        step_info = {"required_skills": ["skill-a"]}
        registry = _registry_with_chains({"skill-a": [skill_a]})

        rendered = _render_required_skills(step_info, registry)

        assert '<skill name="skill-a"' in rendered
        assert 'directory="/fake/skills/skill-a"' in rendered
        assert "</skill>" in rendered


# ---------------------------------------------------------------------------
# Handler tests with real repository
# ---------------------------------------------------------------------------


class TestStartWorkflow:
    @pytest.mark.asyncio
    async def test_start_workflow_success(self, repository, mock_registry, tmp_path):
        result = await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "Workflow started" in text
        assert "test-workflow" in text
        assert "TodoWrite" in text
        assert "list_active_workflows" in text

        # Verify scratchpad was created
        scratchpads = list((tmp_path / ".tachikoma" / "scratchpads").glob("workflow-*.md"))
        assert len(scratchpads) == 1

    @pytest.mark.asyncio
    async def test_start_workflow_invalid_skill(self, repository, mock_registry, tmp_path):
        mock_registry.get_workflow.return_value = None

        result = await handle_start_workflow(
            "no-skill",
            "no-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_start_workflow_empty_steps(self, repository, mock_registry, tmp_path):
        mock_registry.get_workflow.return_value = _make_workflow(steps=[])

        result = await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        assert result.get("is_error") is True
        assert "no steps" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_start_workflow_duplicate_prevention(self, repository, mock_registry, tmp_path):
        # Create first workflow
        await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        # Try to create second
        result = await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        assert result.get("is_error") is True
        assert "already active" in result["content"][0]["text"]


class TestUpdateWorkflowState:
    @pytest.mark.asyncio
    async def test_start_step(self, repository, tmp_path, mock_registry):
        # Create a workflow state directly
        state = _make_state(
            step_states={
                "01-plan": "pending",
                "02-execute": "pending",
                "03-review": "pending",
            }
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "start",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        assert "started" in result["content"][0]["text"].lower()

        # Verify state was updated
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "started"
        assert updated.current_step == "01-plan"

    @pytest.mark.asyncio
    async def test_complete_step(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "started",
                "02-execute": "pending",
                "03-review": "pending",
            },
            definition_snapshot=[
                {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "required": True,
                    "path": "/fake/02-execute",
                },
                {
                    "id": "03-review",
                    "title": "Review",
                    "required": False,
                    "path": "/fake/03-review",
                },
            ],
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "complete",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "02-execute" in text
        assert "started" in text.lower()

        # Verify state: next step auto-started
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "completed"
        assert updated.step_states["02-execute"] == "started"
        assert updated.current_step == "02-execute"

    @pytest.mark.asyncio
    async def test_skip_non_required_step(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "completed",
                "02-execute": "pending",
                "03-review": "pending",
            },
            definition_snapshot=[
                {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "required": False,
                    "path": "/fake/02-execute",
                },
                {
                    "id": "03-review",
                    "title": "Review",
                    "required": True,
                    "path": "/fake/03-review",
                },
            ],
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "02-execute",
            "skip",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        assert "03-review" in result["content"][0]["text"]

        # Verify state: next step auto-started
        updated = await repository.get(state.id)
        assert updated.step_states["02-execute"] == "skipped"
        assert updated.step_states["03-review"] == "started"
        assert updated.current_step == "03-review"

    @pytest.mark.asyncio
    async def test_skip_required_step_rejected(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "pending",
                "02-execute": "pending",
                "03-review": "pending",
            }
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "skip",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True
        assert "required and cannot be skipped" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_complete_pending_rejected(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "pending",
                "02-execute": "pending",
                "03-review": "pending",
            }
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "complete",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True
        assert "Must start" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_invalid_workflow_id(self, repository, tmp_path, mock_registry):
        result = await handle_update_workflow_state(
            "nonexistent",
            "01-plan",
            "start",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_workflow_completion_auto_finalizes(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "completed",
                "02-execute": "started",
                "03-review": "completed",
            }
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "02-execute",
            "complete",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "complete" in text.lower()
        assert "finalized" in text.lower()
        assert "end_workflow" not in text

        # Verify auto-finalized: workflow is soft-deleted
        assert await repository.get(state.id) is None

    @pytest.mark.asyncio
    async def test_complete_last_step_auto_finalizes_with_scratchpad(
        self, repository, tmp_path, mock_registry
    ):
        # Create a scratchpad file on disk
        scratchpad = tmp_path / "scratchpad-auto-finalize.md"
        scratchpad.write_text("# Workflow test\n\nWorkflow ID: test-wf-id\n")

        # Two-step workflow: first done, second in progress
        state = _make_state(
            step_states={"01-plan": "completed", "02-execute": "started"},
            definition_snapshot=[
                {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "required": True,
                    "path": "/fake/02-execute",
                },
            ],
            scratchpad_path=str(scratchpad),
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "02-execute",
            "complete",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "finalized" in text.lower()
        assert "end_workflow" not in text

        # Verify soft-deleted and scratchpad cleaned up
        assert await repository.get(state.id) is None
        assert not scratchpad.exists()

    @pytest.mark.asyncio
    async def test_completed_step_rejects_update(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "completed",
                "02-execute": "pending",
                "03-review": "pending",
            }
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "start",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True
        assert "already completed" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_concurrent_workflows_independent(self, repository, tmp_path, mock_registry):
        # Create two independent workflows
        state_a = _make_state(workflow_id="wf-a", workflow_name="workflow-a")
        state_b = _make_state(workflow_id="wf-b", workflow_name="workflow-b")
        await repository.create(state_a)
        await repository.create(state_b)

        # Update workflow A
        result = await handle_update_workflow_state(
            "wf-a",
            "01-plan",
            "start",
            repository,
            mock_registry,
        )
        assert result.get("is_error") is None

        # Verify B is unchanged
        state_b_check = await repository.get("wf-b")
        assert state_b_check.step_states["01-plan"] == "pending"

    @pytest.mark.asyncio
    async def test_start_injects_required_skills(self, repository, tmp_path):
        # Step 01-plan declares required_skills; registry resolves skill-a
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": ["skill-a"],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": [],
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        registry = _registry_with_chains({"skill-a": [_make_skill("skill-a", "body-a")]})

        result = await handle_update_workflow_state(
            state.id, "01-plan", "start", repository, registry
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "## Required Skills" in text
        assert 'name="skill-a"' in text
        assert "body-a" in text

    @pytest.mark.asyncio
    async def test_complete_auto_start_injects_next_step_required_skills(
        self, repository, tmp_path
    ):
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": ["skill-b"],
            },
        ]
        state = _make_state(
            step_states={"01-plan": "started", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        registry = _registry_with_chains({"skill-b": [_make_skill("skill-b", "body-b")]})

        result = await handle_update_workflow_state(
            state.id, "01-plan", "complete", repository, registry
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "## Required Skills" in text
        assert 'name="skill-b"' in text
        assert "body-b" in text

    @pytest.mark.asyncio
    async def test_skip_auto_start_injects_next_step_required_skills(self, repository, tmp_path):
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": False,
                "path": "/fake/01-plan",
                "required_skills": [],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": ["skill-c"],
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        registry = _registry_with_chains({"skill-c": [_make_skill("skill-c", "body-c")]})

        result = await handle_update_workflow_state(
            state.id, "01-plan", "skip", repository, registry
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "## Required Skills" in text
        assert "body-c" in text

    @pytest.mark.asyncio
    async def test_start_without_required_skills_has_no_block(
        self, repository, tmp_path, mock_registry
    ):
        # Step without required_skills produces the original response format
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
            }
        ]
        state = _make_state(
            step_states={"01-plan": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id, "01-plan", "start", repository, mock_registry
        )

        text = result["content"][0]["text"]
        assert "## Required Skills" not in text
        mock_registry.resolve_chain.assert_not_called()


class TestGetWorkflowState:
    @pytest.mark.asyncio
    async def test_get_existing_state(self, repository, tmp_path):
        state = _make_state()
        await repository.create(state)

        result = await handle_get_workflow_state(state.id, repository)

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert state.id in text
        assert "test-skill" in text
        assert "01-plan" in text

    @pytest.mark.asyncio
    async def test_get_nonexistent_state(self, repository, tmp_path):
        result = await handle_get_workflow_state("nonexistent", repository)

        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"]


class TestEndWorkflow:
    @pytest.mark.asyncio
    async def test_end_complete(self, repository, tmp_path):
        state = _make_state()
        await repository.create(state)

        # Create the scratchpad file
        scratchpad = tmp_path / "scratchpad-test.md"
        scratchpad.write_text("test")
        state = await repository.update(state.id, scratchpad_path=str(scratchpad))

        result = await handle_end_workflow(
            state.id,
            "complete",
            repository,
            tmp_path,
        )

        assert result.get("is_error") is None
        assert "completed" in result["content"][0]["text"]

        # Verify soft-deleted
        assert await repository.get(state.id) is None

    @pytest.mark.asyncio
    async def test_end_abort(self, repository, tmp_path):
        state = _make_state()
        await repository.create(state)

        result = await handle_end_workflow(
            state.id,
            "abort",
            repository,
            tmp_path,
        )

        assert result.get("is_error") is None
        assert "aborted" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_end_nonexistent(self, repository, tmp_path):
        result = await handle_end_workflow(
            "nonexistent",
            "complete",
            repository,
            tmp_path,
        )

        assert result.get("is_error") is True
        assert "not found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_end_deletes_scratchpad(self, repository, tmp_path):
        scratchpad = tmp_path / "scratchpad-delete-test.md"
        scratchpad.write_text("test content")

        state = _make_state()
        state = WorkflowState(
            id=state.id,
            skill_name=state.skill_name,
            workflow_name=state.workflow_name,
            current_step=state.current_step,
            step_states=state.step_states,
            definition_snapshot=state.definition_snapshot,
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=state.created_at,
            updated_at=state.updated_at,
        )
        await repository.create(state)

        await handle_end_workflow(state.id, "complete", repository, tmp_path)

        assert not scratchpad.exists()


class TestListActiveWorkflows:
    @pytest.mark.asyncio
    async def test_list_empty(self, repository, tmp_path):
        result = await handle_list_active_workflows(repository)

        assert result.get("is_error") is None
        assert "No active workflows" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_list_with_active(self, repository, tmp_path):
        state_a = _make_state(workflow_id="wf-a", workflow_name="workflow-a")
        state_b = _make_state(workflow_id="wf-b", workflow_name="workflow-b")
        await repository.create(state_a)
        await repository.create(state_b)

        result = await handle_list_active_workflows(repository)

        text = result["content"][0]["text"]
        assert "workflow-a" in text
        assert "workflow-b" in text

    @pytest.mark.asyncio
    async def test_list_excludes_deleted(self, repository, tmp_path):
        state = _make_state(workflow_id="wf-a")
        await repository.create(state)

        await repository.soft_delete("wf-a")

        result = await handle_list_active_workflows(repository)
        assert "No active workflows" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# Old snapshot compat (skippable → required migration)
# ---------------------------------------------------------------------------


class TestOldSnapshotCompat:
    def test_old_snapshot_skippable_true_allows_skip(self):
        """Old snapshot with skippable=true (no required key) should allow skip."""
        old_snapshot = [
            {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
            {"id": "02-execute", "title": "Execute", "skippable": True, "path": "/fake/02-execute"},
        ]
        states = {"01-plan": "completed", "02-execute": "pending"}

        # skippable=True → required=False, skip allowed
        assert validate_transition(states, "02-execute", "skip", old_snapshot) is None

    def test_old_snapshot_skippable_false_rejects_skip(self):
        """Old snapshot with skippable=false (no required key) should reject skip."""
        old_snapshot = [
            {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
        ]
        states = {"01-plan": "pending"}

        error = validate_transition(states, "01-plan", "skip", old_snapshot)
        assert error is not None
        assert "required and cannot be skipped" in error

    def test_new_snapshot_required_key_takes_precedence(self):
        """New snapshot with required key should ignore any residual skippable."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "skippable": True,
                "path": "/fake/01-plan",
            },
        ]
        states = {"01-plan": "pending"}

        error = validate_transition(states, "01-plan", "skip", snapshot)
        assert error is not None
        assert "required and cannot be skipped" in error


class TestStartWorkflowMarker:
    @pytest.mark.asyncio
    async def test_start_workflow_shows_marker_for_non_required(
        self, repository, mock_registry, tmp_path
    ):
        """Steps with required=False show (skippable) marker."""
        steps = [
            _make_step("01-plan", "Plan", required=True),
            _make_step("02-execute", "Execute", required=False),
            _make_step("03-review", "Review", required=True),
        ]
        workflow = _make_workflow(steps=steps)
        mock_registry.get_workflow.return_value = workflow
        mock_registry.workflows = {("test-skill", "test-workflow"): workflow}

        result = await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        text = result["content"][0]["text"]
        # Step with required=False should show marker
        assert "02-execute" in text
        assert "(skippable)" in text
        # Count markers — only one step has required=False
        assert text.count("(skippable)") == 1

    @pytest.mark.asyncio
    async def test_start_workflow_shows_condition_marker(self, repository, mock_registry, tmp_path):
        """Steps with condition show (if: ...) marker."""
        steps = [
            _make_step("01-plan", "Plan"),
            _make_step("02-deploy", "Deploy", condition="Only run if plan exists"),
        ]
        workflow = _make_workflow(steps=steps)
        mock_registry.get_workflow.return_value = workflow
        mock_registry.workflows = {("test-skill", "test-workflow"): workflow}

        result = await handle_start_workflow(
            "test-skill",
            "test-workflow",
            mock_registry,
            repository,
            tmp_path,
        )

        text = result["content"][0]["text"]
        assert "(if: Only run if plan exists)" in text
        # Step without condition should not have the marker
        lines = text.splitlines()
        plan_line = [line for line in lines if "01-plan" in line][0]
        assert "(if:" not in plan_line


# ---------------------------------------------------------------------------
# Condition evaluation in handlers
# ---------------------------------------------------------------------------


def _make_defaults():
    """Create a minimal AgentDefaults for testing."""
    return AgentDefaults(
        cwd=Path("/tmp/test-workspace"),
        cli_path=Path("/usr/local/bin/claude"),
        env={},
    )


def _make_session_context(session_id: str | None = "test-sdk-session"):
    """Create a SessionContext with an optional session ID."""
    ctx = SessionContext()
    ctx.set(session_id)
    return ctx


class TestEvaluateAndAdvance:
    @pytest.mark.asyncio
    async def test_no_condition_starts_immediately(self):
        """Steps without condition start immediately."""
        snapshot = [
            {"id": "01-plan", "title": "Plan", "required": True, "condition": None},
        ]
        step_states = {"01-plan": "pending"}

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
        ) as mock_eval:
            result_step, skipped = await _evaluate_and_advance(
                step_states=step_states,
                definition_snapshot=snapshot,
                scratchpad_path="/tmp/scratch.md",
                workspace_path=Path("/tmp"),
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
            )

        assert result_step == "01-plan"
        assert skipped == []
        mock_eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_condition_passes_starts_step(self):
        """Steps with passing condition start."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "condition": "Check if file exists",
            },
        ]
        step_states = {"01-plan": "pending"}

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=True, is_error=False, reason="file exists"),
        ) as mock_eval:
            result_step, skipped = await _evaluate_and_advance(
                step_states=step_states,
                definition_snapshot=snapshot,
                scratchpad_path="/tmp/scratch.md",
                workspace_path=Path("/tmp"),
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
            )

        assert result_step == "01-plan"
        assert skipped == []
        mock_eval.assert_called_once()

    @pytest.mark.asyncio
    async def test_condition_fails_skips_step(self):
        """Steps with failing condition are skipped."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "condition": "Check if file exists",
            },
            {"id": "02-execute", "title": "Execute", "required": True, "condition": None},
        ]
        step_states = {"01-plan": "pending", "02-execute": "pending"}

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="no file"),
        ):
            result_step, skipped = await _evaluate_and_advance(
                step_states=step_states,
                definition_snapshot=snapshot,
                scratchpad_path="/tmp/scratch.md",
                workspace_path=Path("/tmp"),
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
            )

        assert result_step == "02-execute"
        assert len(skipped) == 1
        assert skipped[0][0] == "01-plan"
        assert skipped[0][1].passes is False
        assert step_states["01-plan"] == "skipped"

    @pytest.mark.asyncio
    async def test_consecutive_false_conditions_all_skipped(self):
        """Multiple consecutive failing conditions are all skipped."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "condition": "Check A",
            },
            {
                "id": "02-deploy",
                "title": "Deploy",
                "required": True,
                "condition": "Check B",
            },
            {"id": "03-review", "title": "Review", "required": True, "condition": None},
        ]
        step_states = {
            "01-plan": "pending",
            "02-deploy": "pending",
            "03-review": "pending",
        }

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="not met"),
        ):
            result_step, skipped = await _evaluate_and_advance(
                step_states=step_states,
                definition_snapshot=snapshot,
                scratchpad_path="/tmp/scratch.md",
                workspace_path=Path("/tmp"),
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
            )

        assert result_step == "03-review"
        assert len(skipped) == 2
        assert [s[0] for s in skipped] == ["01-plan", "02-deploy"]

    @pytest.mark.asyncio
    async def test_all_false_conditions_returns_none(self):
        """All steps with false conditions returns None (auto-finalize)."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "condition": "Check A",
            },
            {
                "id": "02-deploy",
                "title": "Deploy",
                "required": True,
                "condition": "Check B",
            },
        ]
        step_states = {"01-plan": "pending", "02-deploy": "pending"}

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="not met"),
        ):
            result_step, skipped = await _evaluate_and_advance(
                step_states=step_states,
                definition_snapshot=snapshot,
                scratchpad_path="/tmp/scratch.md",
                workspace_path=Path("/tmp"),
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
            )

        assert result_step is None
        assert len(skipped) == 2


class TestConditionHandlerIntegration:
    """Tests for handle_update_workflow_state with condition support."""

    @pytest.mark.asyncio
    async def test_explicit_start_with_passing_condition(self, repository, mock_registry):
        """Explicit start with passing condition proceeds normally."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": "Check if file exists",
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=True, is_error=False, reason="file exists"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "start",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        assert result.get("is_error") is None
        assert "started" in result["content"][0]["text"].lower()
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "started"

    @pytest.mark.asyncio
    async def test_explicit_start_with_failing_condition_skips(self, repository, mock_registry):
        """Explicit start with failing condition auto-skips."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": "Check if file exists",
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": [],
                "condition": None,
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="no file"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "start",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        assert result.get("is_error") is None
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "skipped"
        assert updated.step_states["02-execute"] == "started"
        assert updated.current_step == "02-execute"

    @pytest.mark.asyncio
    async def test_required_step_with_false_condition_still_skipped(
        self, repository, mock_registry
    ):
        """A required step with a false condition is still auto-skipped."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": "Check if file exists",
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="no file"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "start",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        # Auto-finalized because all steps are done
        assert result.get("is_error") is None
        assert "finalized" in result["content"][0]["text"].lower()
        # Verify step was skipped despite being required
        assert await repository.get(state.id) is None

    @pytest.mark.asyncio
    async def test_complete_advances_with_condition_check(self, repository, mock_registry):
        """Completing a step evaluates conditions on the next step."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": None,
            },
            {
                "id": "02-deploy",
                "title": "Deploy",
                "required": True,
                "path": "/fake/02-deploy",
                "required_skills": [],
                "condition": "Check if plan was written",
            },
            {
                "id": "03-review",
                "title": "Review",
                "required": True,
                "path": "/fake/03-review",
                "required_skills": [],
                "condition": None,
            },
        ]
        state = _make_state(
            step_states={
                "01-plan": "started",
                "02-deploy": "pending",
                "03-review": "pending",
            },
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="no plan"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "complete",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert "03-review" in text
        assert "started" in text.lower()

        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "completed"
        assert updated.step_states["02-deploy"] == "skipped"
        assert updated.step_states["03-review"] == "started"

    @pytest.mark.asyncio
    async def test_all_steps_false_auto_finalizes(self, repository, mock_registry):
        """All steps with unmet conditions auto-finalizes the workflow."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": "Check A",
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": [],
                "condition": "Check B",
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=False, reason="not met"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "start",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        assert result.get("is_error") is None
        assert "finalized" in result["content"][0]["text"].lower()
        assert "Condition-Skipped Steps" in result["content"][0]["text"]
        assert await repository.get(state.id) is None

    @pytest.mark.asyncio
    async def test_no_condition_support_backward_compatible(self, repository, mock_registry):
        """Without condition support params, behaves as before."""
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "start",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is None
        assert "started" in result["content"][0]["text"].lower()
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "started"

    @pytest.mark.asyncio
    async def test_condition_error_skips_step(self, repository, mock_registry):
        """Condition evaluation error causes step to be skipped (fail closed)."""
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "required": True,
                "path": "/fake/01-plan",
                "required_skills": [],
                "condition": "Check if file exists",
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "required": True,
                "path": "/fake/02-execute",
                "required_skills": [],
                "condition": None,
            },
        ]
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending"},
            definition_snapshot=snapshot,
        )
        await repository.create(state)

        with patch(
            "tachikoma.workflows.tools.evaluate_condition",
            new_callable=AsyncMock,
            return_value=ConditionResult(passes=False, is_error=True, reason="SDK timeout"),
        ):
            result = await handle_update_workflow_state(
                state.id,
                "01-plan",
                "start",
                repository,
                mock_registry,
                agent_defaults=_make_defaults(),
                session_context=_make_session_context(),
                workspace_path=Path("/tmp"),
            )

        assert result.get("is_error") is None
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "skipped"
        assert updated.step_states["02-execute"] == "started"

    @pytest.mark.asyncio
    async def test_old_snapshot_no_condition_key_backward_compatible(
        self, repository, mock_registry
    ):
        """Old snapshots without condition key behave as no condition."""
        state = _make_state(
            step_states={"01-plan": "pending"},
            definition_snapshot=[
                {"id": "01-plan", "title": "Plan", "required": True, "path": "/fake/01-plan"},
            ],
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "start",
            repository,
            mock_registry,
            agent_defaults=_make_defaults(),
            session_context=_make_session_context(),
            workspace_path=Path("/tmp"),
        )

        assert result.get("is_error") is None
        assert "started" in result["content"][0]["text"].lower()
        updated = await repository.get(state.id)
        assert updated.step_states["01-plan"] == "started"


# ---------------------------------------------------------------------------
# Composition (DLT-161) — cascade engine, routing, breadcrumb, nested view
# ---------------------------------------------------------------------------


def _make_compose_step(
    step_id: str,
    title: str,
    composes: str,
    *,
    required: bool = True,
    condition: str | None = None,
) -> StepDefinition:
    """Create a composition step definition (sentinel body in instructions.md)."""
    return StepDefinition(
        id=step_id,
        title=title,
        instructions_path=Path(
            f"/fake/skill/workflows/test/{step_id}/instructions.md"
        ),
        references_path=None,
        scripts_path=None,
        required=required,
        condition=condition,
        composes=composes,
    )


def _registry_with_workflows(
    workflows: dict[tuple[str, str], WorkflowDefinition],
) -> MagicMock:
    """Mock registry that resolves get_workflow against the given dict."""
    registry = MagicMock(spec=SkillRegistry)

    def get_workflow(skill_name: str, workflow_name: str):
        return workflows.get((skill_name, workflow_name))

    registry.get_workflow.side_effect = get_workflow
    registry.workflows = dict(workflows)
    registry.resolve_chain.side_effect = lambda name: (_ for _ in ()).throw(KeyError(name))
    return registry


async def _seed_parent_at_compose_step(
    repository: WorkflowStateRepository,
    *,
    parent_id: str = "parent-wf",
    parent_skill: str = "parent-skill",
    parent_workflow: str = "parent-wf-name",
    compose_step_id: str = "02-compose",
    compose_target: str = "child-skill/child-wf-name",
    scratchpad_path: str | None = None,
) -> WorkflowState:
    """Seed a parent workflow whose 02-compose step is pending and ready to spawn."""
    snapshot = [
        {
            "id": "01-prep",
            "title": "Prep",
            "required": True,
            "path": "/fake/01-prep",
            "required_skills": [],
            "condition": None,
            "composes": None,
        },
        {
            "id": compose_step_id,
            "title": "Run Child",
            "required": True,
            "path": "/fake/02-compose",
            "required_skills": [],
            "condition": None,
            "composes": compose_target,
        },
        {
            "id": "03-finish",
            "title": "Finish",
            "required": True,
            "path": "/fake/03-finish",
            "required_skills": [],
            "condition": None,
            "composes": None,
        },
    ]
    state = WorkflowState(
        id=parent_id,
        skill_name=parent_skill,
        workflow_name=parent_workflow,
        current_step="01-prep",
        step_states={"01-prep": "completed", compose_step_id: "pending", "03-finish": "pending"},
        definition_snapshot=snapshot,
        scratchpad_path=scratchpad_path or f"/tmp/parent-{parent_id}.md",
        deleted_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await repository.create(state)
    return state


def _make_child_workflow(
    *,
    skill_name: str = "child-skill",
    workflow_name: str = "child-wf-name",
    steps: list[StepDefinition] | None = None,
) -> WorkflowDefinition:
    if steps is None:
        steps = [
            _make_step("01-check", "Check"),
            _make_step("02-categorize", "Categorize"),
        ]
    return WorkflowDefinition(
        skill_name=skill_name,
        workflow_name=workflow_name,
        steps=steps,
        path=Path(f"/fake/skills/{skill_name}/workflows/{workflow_name}"),
    )


class TestStepToSnapshotIncludesComposes:
    def test_step_to_snapshot_includes_composes_field(self):
        step = _make_compose_step("02-handle", "Handle", composes="child-wf")
        snapshot = _step_to_snapshot(step)

        assert snapshot["composes"] == "child-wf"

    def test_step_without_composes_serializes_none(self):
        step = _make_step("01-plan", "Plan")
        snapshot = _step_to_snapshot(step)

        assert snapshot["composes"] is None


class TestRenderBreadcrumb:
    def test_single_layer(self):
        rendered = _render_breadcrumb([("weekly-review", "01-plan", None)])
        assert rendered == "weekly-review/01-plan"

    def test_two_layers_uses_separator(self):
        rendered = _render_breadcrumb(
            [
                ("weekly-review", "02-handle-inbox", None),
                ("process-inbox-note", "01-check", None),
            ]
        )
        assert rendered == "weekly-review/02-handle-inbox > process-inbox-note/01-check"

    def test_three_layers(self):
        rendered = _render_breadcrumb(
            [
                ("a", "01", None),
                ("b", "02", None),
                ("c", "03", None),
            ]
        )
        assert rendered == "a/01 > b/02 > c/03"

    def test_empty_returns_empty_string(self):
        assert _render_breadcrumb([]) == ""

    def test_deepest_layer_with_item_suffix(self):
        rendered = _render_breadcrumb(
            [
                ("weekly-review", "03-process", None),
                ("process-item", "01-step", "foo.md"),
            ]
        )
        assert rendered == "weekly-review/03-process > process-item/01-step (item: foo.md)"

    def test_non_deepest_item_not_suffixed(self):
        rendered = _render_breadcrumb(
            [
                ("weekly-review", "03-process", "outer-item"),
                ("process-item", "01-step", "inner-item"),
            ]
        )
        assert (
            rendered
            == "weekly-review/03-process > process-item/01-step (item: inner-item)"
        )

    def test_empty_item_renders_literal(self):
        rendered = _render_breadcrumb([("wf", "01-step", "")])
        assert rendered == "wf/01-step (item: )"


class TestCascadeRouting:
    @pytest.mark.asyncio
    async def test_rejects_child_id_on_update(self, repository, tmp_path):
        # Manually create a child record (parent_workflow_id set)
        child_state = WorkflowState(
            id="child-id",
            skill_name="child-skill",
            workflow_name="child-wf",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path="/tmp/parent-scratch.md",
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id="parent-id",
            parent_step_id="02-compose",
        )
        await repository.create(child_state)

        registry = _registry_with_workflows({})

        result = await handle_update_workflow_state(
            "child-id",
            "01-check",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is True
        assert "composed child" in result["content"][0]["text"]
        assert "top-level" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_rejects_step_typo_with_valid_id_list(
        self, repository, mock_registry
    ):
        state = _make_state(
            step_states={"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        )
        await repository.create(state)

        result = await handle_update_workflow_state(
            state.id,
            "01-plann",  # typo
            "start",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "Invalid step '01-plann'" in text
        assert "01-plan" in text
        assert "test-workflow" in text  # deepest layer name

    @pytest.mark.asyncio
    async def test_rejects_parent_step_while_child_active(
        self, repository, tmp_path
    ):
        # Set up a parent at composition step + active child
        parent = await _seed_parent_at_compose_step(repository)
        # Mark composition step as STARTED
        parent_state = await repository.get(parent.id)
        new_step_states = dict(parent_state.step_states)
        new_step_states["02-compose"] = "started"
        await repository.update(
            parent.id, step_states=new_step_states, current_step="02-compose"
        )

        # Create child via direct repo create
        child = WorkflowState(
            id="child-id",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started", "02-categorize": "pending"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                },
                {
                    "id": "02-categorize",
                    "title": "Categorize",
                    "required": True,
                    "path": "/fake/02-categorize",
                },
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        registry = _registry_with_workflows({})

        # Try to operate on the parent's composition step directly
        result = await handle_update_workflow_state(
            parent.id,
            "02-compose",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "Invalid step '02-compose'" in text
        assert "child-wf-name" in text
        assert "01-check" in text or "02-categorize" in text


class TestCascadeSpawn:
    @pytest.mark.asyncio
    async def test_start_composition_step_spawns_child(self, repository, tmp_path):
        # Parent at the composition step (next pending = 02-compose)
        parent = await _seed_parent_at_compose_step(repository)

        # Mark 01-prep completed so the next pending is 02-compose; we'll start it.
        # The seed already sets 01-prep=completed, 02-compose=pending.

        child_def = _make_child_workflow()
        registry = _registry_with_workflows({("child-skill", "child-wf-name"): child_def})

        result = await handle_update_workflow_state(
            parent.id,
            "02-compose",
            "start",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        # Breadcrumb should include both layers
        assert "parent-wf-name/02-compose" in text
        assert "child-wf-name/01-check" in text
        # Child's first step body would be "Check" (title)
        assert "Check" in text

        # Verify child record was persisted
        chain = await repository.get_active_chain(parent.id)
        assert len(chain) == 2
        assert chain[1].workflow_name == "child-wf-name"
        assert chain[1].parent_workflow_id == parent.id
        assert chain[1].parent_step_id == "02-compose"
        assert chain[1].step_states["01-check"] == "started"
        # Child inherits parent's scratchpad
        assert chain[1].scratchpad_path == parent.scratchpad_path

    @pytest.mark.asyncio
    async def test_composition_step_body_not_used(self, repository, tmp_path):
        """R1 fourth AC: composition step's instructions.md body is NOT included
        in the response — only its frontmatter is honoured. We assert this by
        making the composition step's path unreadable AND verifying the response
        contains the child's first-step content, not the parent's compose-step body.
        """
        # Use a path that doesn't exist for the parent's compose step;
        # _read_step_instructions returns None on FileNotFoundError, so any
        # attempt to read the parent's compose body would yield no extra content.
        parent = await _seed_parent_at_compose_step(repository)

        # Child's first step has a real instructions file with a sentinel
        instructions_dir = tmp_path / "child-step"
        instructions_dir.mkdir()
        sentinel = "CHILD-FIRST-STEP-INSTRUCTIONS-MARKER"
        (instructions_dir / "instructions.md").write_text(sentinel)

        child_first_step = StepDefinition(
            id="01-check",
            title="Check",
            instructions_path=instructions_dir / "instructions.md",
            references_path=None,
            scripts_path=None,
        )
        child_def = _make_child_workflow(steps=[child_first_step])
        registry = _registry_with_workflows({("child-skill", "child-wf-name"): child_def})

        result = await handle_update_workflow_state(
            parent.id,
            "02-compose",
            "start",
            repository,
            registry,
        )

        text = result["content"][0]["text"]
        # The child's instructions appear (sentinel)
        assert sentinel in text

    @pytest.mark.asyncio
    async def test_corruption_on_missing_target(self, repository, tmp_path):
        parent = await _seed_parent_at_compose_step(
            repository, compose_target="missing-skill/missing-wf"
        )
        # Registry has no workflows
        registry = _registry_with_workflows({})

        result = await handle_update_workflow_state(
            parent.id,
            "02-compose",
            "start",
            repository,
            registry,
        )

        assert result.get("is_error") is True
        text = result["content"][0]["text"]
        assert "no longer exists" in text or "not registered" in text or "Composition" in text
        assert "abort" in text.lower()


class TestCascadeAutoResume:
    @pytest.mark.asyncio
    async def test_complete_last_child_step_resumes_parent(
        self, repository, tmp_path
    ):
        # Parent has 02-compose started; child at last step started
        parent = await _seed_parent_at_compose_step(repository)
        new_states = dict(parent.step_states)
        new_states["02-compose"] = "started"
        await repository.update(
            parent.id, step_states=new_states, current_step="02-compose"
        )

        child_id = "child-id"
        child = WorkflowState(
            id=child_id,
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="02-categorize",
            step_states={"01-check": "completed", "02-categorize": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                },
                {
                    "id": "02-categorize",
                    "title": "Categorize",
                    "required": True,
                    "path": "/fake/02-categorize",
                },
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        registry = _registry_with_workflows({})

        # Complete the last child step; expect cascade to soft-delete child
        # AND advance parent to 03-finish in a single tool response.
        result = await handle_update_workflow_state(
            parent.id,
            "02-categorize",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        # Now back at the parent's next step
        assert "03-finish" in text
        # Breadcrumb is just the parent (child is gone)
        assert "parent-wf-name/03-finish" in text
        assert "child-wf-name" not in text.split("\n\n", 1)[0]  # not in breadcrumb line

        # Child must be soft-deleted; parent advanced
        chain = await repository.get_active_chain(parent.id)
        assert len(chain) == 1
        assert chain[0].id == parent.id
        assert chain[0].step_states["02-compose"] == "completed"
        assert chain[0].step_states["03-finish"] == "started"

        # Direct child fetch returns None (soft-deleted)
        assert await repository.get(child_id) is None

    @pytest.mark.asyncio
    async def test_complete_last_child_step_finalizes_parent_when_last(
        self, repository, tmp_path
    ):
        """If the parent's only remaining step is the composition step, its
        completion via child auto-resume should auto-finalize the entire stack.
        """
        scratchpad = tmp_path / "scratch.md"
        scratchpad.write_text("# scratch")

        # Parent has only one step (composition), in started state
        snapshot = [
            {
                "id": "01-compose",
                "title": "Run Child",
                "required": True,
                "path": "/fake/01-compose",
                "required_skills": [],
                "condition": None,
                "composes": "child-wf-name",
            }
        ]
        parent_state = WorkflowState(
            id="parent-only-compose",
            skill_name="parent-skill",
            workflow_name="parent-only",
            current_step="01-compose",
            step_states={"01-compose": "started"},
            definition_snapshot=snapshot,
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repository.create(parent_state)

        child = WorkflowState(
            id="child-only",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent_state.id,
            parent_step_id="01-compose",
        )
        await repository.create(child)

        registry = _registry_with_workflows({})

        result = await handle_update_workflow_state(
            parent_state.id,
            "01-check",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        assert "finalized" in result["content"][0]["text"].lower()
        # Both records gone, scratchpad gone
        assert await repository.get(parent_state.id) is None
        assert await repository.get(child.id) is None
        assert not scratchpad.exists()

    @pytest.mark.asyncio
    async def test_two_composition_steps_in_a_row(self, repository, tmp_path):
        """Child finalizes; parent's next step is itself a composition →
        a new child should be spawned in the same tool call.
        """
        # Parent: [01-prep done, 02-compose-a (composition, started),
        #          03-compose-b (composition, pending), 04-finish (pending)]
        snapshot = [
            {
                "id": "01-prep",
                "title": "Prep",
                "required": True,
                "path": "/fake/01-prep",
                "required_skills": [],
                "condition": None,
                "composes": None,
            },
            {
                "id": "02-compose-a",
                "title": "Compose A",
                "required": True,
                "path": "/fake/02-compose-a",
                "required_skills": [],
                "condition": None,
                "composes": "child-a",
            },
            {
                "id": "03-compose-b",
                "title": "Compose B",
                "required": True,
                "path": "/fake/03-compose-b",
                "required_skills": [],
                "condition": None,
                "composes": "child-b",
            },
            {
                "id": "04-finish",
                "title": "Finish",
                "required": True,
                "path": "/fake/04-finish",
                "required_skills": [],
                "condition": None,
                "composes": None,
            },
        ]
        parent = WorkflowState(
            id="parent-x",
            skill_name="parent-skill",
            workflow_name="parent-x-name",
            current_step="02-compose-a",
            step_states={
                "01-prep": "completed",
                "02-compose-a": "started",
                "03-compose-b": "pending",
                "04-finish": "pending",
            },
            definition_snapshot=snapshot,
            scratchpad_path=f"{tmp_path}/parent-x.md",
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repository.create(parent)

        child_a = WorkflowState(
            id="child-a-id",
            skill_name="parent-skill",
            workflow_name="child-a",
            current_step="01-only",
            step_states={"01-only": "started"},
            definition_snapshot=[
                {
                    "id": "01-only",
                    "title": "Only A",
                    "required": True,
                    "path": "/fake/01-only-a",
                }
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose-a",
        )
        await repository.create(child_a)

        # child-b is registered in the registry only — it gets spawned
        child_b_def = WorkflowDefinition(
            skill_name="parent-skill",
            workflow_name="child-b",
            steps=[_make_step("01-only", "Only B")],
            path=Path("/fake/parent-skill/workflows/child-b"),
        )
        registry = _registry_with_workflows({("parent-skill", "child-b"): child_b_def})

        # Complete child-a's last step. This should:
        #  - finalize child-a, complete 02-compose-a
        #  - find 03-compose-b as next pending → spawn child-b
        #  - start child-b's 01-only
        result = await handle_update_workflow_state(
            parent.id,
            "01-only",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        # Breadcrumb shows parent at 03-compose-b > child-b/01-only
        assert "parent-x-name/03-compose-b" in text
        assert "child-b/01-only" in text

        # child-a soft-deleted; child-b active
        chain = await repository.get_active_chain(parent.id)
        assert len(chain) == 2
        assert chain[1].workflow_name == "child-b"
        assert chain[1].step_states["01-only"] == "started"
        assert await repository.get("child-a-id") is None

    @pytest.mark.asyncio
    async def test_three_levels_deep(self, repository, tmp_path):
        """A → B → C running; complete C's last step → B advances."""
        scratchpad = tmp_path / "scratch-3.md"
        scratchpad.write_text("# scratch")

        # A has step 01-only (composition → B), started
        a = WorkflowState(
            id="a-id",
            skill_name="s",
            workflow_name="a",
            current_step="01-compose",
            step_states={"01-compose": "started", "02-finish": "pending"},
            definition_snapshot=[
                {
                    "id": "01-compose",
                    "title": "Run B",
                    "required": True,
                    "path": "/fake/a/01",
                    "composes": "b",
                },
                {
                    "id": "02-finish",
                    "title": "Finish A",
                    "required": True,
                    "path": "/fake/a/02",
                    "composes": None,
                },
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repository.create(a)

        b = WorkflowState(
            id="b-id",
            skill_name="s",
            workflow_name="b",
            current_step="01-compose",
            step_states={"01-compose": "started", "02-finish-b": "pending"},
            definition_snapshot=[
                {
                    "id": "01-compose",
                    "title": "Run C",
                    "required": True,
                    "path": "/fake/b/01",
                    "composes": "c",
                },
                {
                    "id": "02-finish-b",
                    "title": "Finish B",
                    "required": True,
                    "path": "/fake/b/02",
                    "composes": None,
                },
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=a.id,
            parent_step_id="01-compose",
        )
        await repository.create(b)

        c = WorkflowState(
            id="c-id",
            skill_name="s",
            workflow_name="c",
            current_step="01-only",
            step_states={"01-only": "started"},
            definition_snapshot=[
                {
                    "id": "01-only",
                    "title": "Only C",
                    "required": True,
                    "path": "/fake/c/01",
                    "composes": None,
                }
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=b.id,
            parent_step_id="01-compose",
        )
        await repository.create(c)

        registry = _registry_with_workflows({})

        # Complete C's last step → cascade: C deleted, B's 01-compose completed,
        # B advances to 02-finish-b (started), terminates.
        result = await handle_update_workflow_state(
            a.id,
            "01-only",
            "complete",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        # Breadcrumb: a at 01-compose > b at 02-finish-b
        assert "a/01-compose" in text
        assert "b/02-finish-b" in text

        # C is gone; A and B remain active
        assert await repository.get(c.id) is None
        chain = await repository.get_active_chain(a.id)
        assert len(chain) == 2
        assert chain[1].id == b.id
        assert chain[1].step_states["02-finish-b"] == "started"
        assert chain[1].step_states["01-compose"] == "completed"


class TestCompositionStepGating:
    @pytest.mark.asyncio
    async def test_skip_composition_step_when_optional_does_not_spawn(
        self, repository, tmp_path
    ):
        """R3 / R14: required:false + skip → no child created, parent advances."""
        snapshot = [
            {
                "id": "01-compose",
                "title": "Run Child (optional)",
                "required": False,
                "path": "/fake/01-compose",
                "required_skills": [],
                "condition": None,
                "composes": "child-wf",
            },
            {
                "id": "02-finish",
                "title": "Finish",
                "required": True,
                "path": "/fake/02-finish",
                "required_skills": [],
                "condition": None,
                "composes": None,
            },
        ]
        parent = WorkflowState(
            id="parent-skip",
            skill_name="s",
            workflow_name="parent-skip-wf",
            current_step=None,
            step_states={"01-compose": "pending", "02-finish": "pending"},
            definition_snapshot=snapshot,
            scratchpad_path=f"{tmp_path}/parent-skip.md",
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        await repository.create(parent)

        registry = _registry_with_workflows({})

        result = await handle_update_workflow_state(
            parent.id,
            "01-compose",
            "skip",
            repository,
            registry,
        )

        assert result.get("is_error") is None
        # No child record was created
        chain = await repository.get_active_chain(parent.id)
        assert len(chain) == 1
        # Parent advanced to 02-finish
        assert chain[0].step_states["01-compose"] == "skipped"
        assert chain[0].step_states["02-finish"] == "started"

    @pytest.mark.asyncio
    async def test_skip_required_composition_step_rejected(
        self, repository, tmp_path
    ):
        """R3 third AC: skip on required composition step → 'is required' error."""
        parent = await _seed_parent_at_compose_step(repository)

        registry = _registry_with_workflows({})

        result = await handle_update_workflow_state(
            parent.id,
            "02-compose",
            "skip",
            repository,
            registry,
        )

        assert result.get("is_error") is True
        assert "required" in result["content"][0]["text"]

        # No child was created
        chain = await repository.get_active_chain(parent.id)
        assert len(chain) == 1


class TestSharedScratchpad:
    @pytest.mark.asyncio
    async def test_two_parents_keep_scratchpads_isolated(
        self, repository, tmp_path
    ):
        """R9: two parents each composing their own child have isolated
        scratchpads — aborting one does not touch the other.
        """
        scratch_a = tmp_path / "parent-a.md"
        scratch_a.write_text("# a")
        scratch_b = tmp_path / "parent-b.md"
        scratch_b.write_text("# b")

        # Two independent parents, each with an active child of the same workflow
        parent_a = await _seed_parent_at_compose_step(
            repository,
            parent_id="parent-a",
            parent_workflow="parent-a-wf",
            scratchpad_path=str(scratch_a),
        )
        parent_b = await _seed_parent_at_compose_step(
            repository,
            parent_id="parent-b",
            parent_workflow="parent-b-wf",
            scratchpad_path=str(scratch_b),
        )

        # Mark both compose steps as STARTED, then create their respective children
        for p in (parent_a, parent_b):
            ss = dict(p.step_states)
            ss["02-compose"] = "started"
            await repository.update(p.id, step_states=ss, current_step="02-compose")

        child_a = WorkflowState(
            id="child-a",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=str(scratch_a),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent_a.id,
            parent_step_id="02-compose",
        )
        child_b = WorkflowState(
            id="child-b",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=str(scratch_b),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent_b.id,
            parent_step_id="02-compose",
        )
        await repository.create(child_a)
        await repository.create(child_b)

        # Abort parent A
        await handle_end_workflow(parent_a.id, "abort", repository, tmp_path)

        # parent_a's scratchpad gone, parent_b's untouched
        assert not scratch_a.exists()
        assert scratch_b.exists()

        # child_a soft-deleted; child_b still active
        assert await repository.get("child-a") is None
        chain_b = await repository.get_active_chain(parent_b.id)
        assert len(chain_b) == 2
        assert chain_b[1].id == "child-b"


class TestNestedGetWorkflowState:
    @pytest.mark.asyncio
    async def test_top_level_only_no_active_child_section(self, repository):
        state = _make_state()
        await repository.create(state)

        result = await handle_get_workflow_state(state.id, repository)

        assert result.get("is_error") is None
        text = result["content"][0]["text"]
        assert state.id in text
        assert "Active Child" not in text

    @pytest.mark.asyncio
    async def test_with_active_child_inlines_path(self, repository, tmp_path):
        parent = await _seed_parent_at_compose_step(repository)
        # Mark composition step started
        ss = dict(parent.step_states)
        ss["02-compose"] = "started"
        await repository.update(parent.id, step_states=ss, current_step="02-compose")

        child = WorkflowState(
            id="child-id",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started", "02-categorize": "pending"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                },
                {
                    "id": "02-categorize",
                    "title": "Categorize",
                    "required": True,
                    "path": "/fake/02-categorize",
                },
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        result = await handle_get_workflow_state(parent.id, repository)
        text = result["content"][0]["text"]

        assert "Active Child" in text
        assert "child-wf-name" in text
        assert "01-check" in text
        # Breadcrumb at top
        assert "parent-wf-name/02-compose" in text
        assert "child-wf-name/01-check" in text

    @pytest.mark.asyncio
    async def test_child_id_returns_standalone_with_note(self, repository, tmp_path):
        """R12 fifth AC: calling get_workflow_state with a child ID returns
        the standalone view + a note pointing at the parent.
        """
        parent = await _seed_parent_at_compose_step(repository)
        child = WorkflowState(
            id="child-only",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        result = await handle_get_workflow_state(child.id, repository)
        text = result["content"][0]["text"]

        assert child.id in text
        assert "composed child" in text
        assert "top-level" in text or "Parent workflow ID" in text
        assert parent.id in text

    @pytest.mark.asyncio
    async def test_corruption_warning_on_missing_target(self, repository, tmp_path):
        """R5 fourth AC: if an active composition step's target is no longer
        registered, get_workflow_state surfaces a corruption warning.
        """
        # Parent has 02-compose started but the registry has no matching workflow
        parent = await _seed_parent_at_compose_step(
            repository, compose_target="missing/missing-wf"
        )
        ss = dict(parent.step_states)
        ss["02-compose"] = "started"
        await repository.update(parent.id, step_states=ss, current_step="02-compose")

        registry = _registry_with_workflows({})

        result = await handle_get_workflow_state(parent.id, repository, registry)
        text = result["content"][0]["text"]

        assert "corruption" in text.lower()
        assert "missing/missing-wf" in text
        assert "abort" in text.lower()


class TestEndWorkflowAbortCascade:
    @pytest.mark.asyncio
    async def test_abort_with_active_child_cascades(self, repository, tmp_path):
        scratch = tmp_path / "abort-scratch.md"
        scratch.write_text("# abort")

        parent = await _seed_parent_at_compose_step(
            repository, scratchpad_path=str(scratch)
        )
        child = WorkflowState(
            id="child-abort",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=str(scratch),
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        result = await handle_end_workflow(
            parent.id, "abort", repository, tmp_path
        )

        assert result.get("is_error") is None
        # Both gone, scratchpad gone
        assert await repository.get(parent.id) is None
        assert await repository.get(child.id) is None
        assert not scratch.exists()

    @pytest.mark.asyncio
    async def test_end_rejects_child_id(self, repository, tmp_path):
        # Create a parent + child; try to end the child
        parent = await _seed_parent_at_compose_step(repository)
        child = WorkflowState(
            id="child-end-reject",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        result = await handle_end_workflow(
            child.id, "abort", repository, tmp_path
        )

        assert result.get("is_error") is True
        assert "composed child" in result["content"][0]["text"]


class TestListActiveExcludesChildren:
    @pytest.mark.asyncio
    async def test_list_active_returns_only_top_level(self, repository, tmp_path):
        parent = await _seed_parent_at_compose_step(repository)
        child = WorkflowState(
            id="child-list",
            skill_name="child-skill",
            workflow_name="child-wf-name",
            current_step="01-check",
            step_states={"01-check": "started"},
            definition_snapshot=[
                {
                    "id": "01-check",
                    "title": "Check",
                    "required": True,
                    "path": "/fake/01-check",
                }
            ],
            scratchpad_path=parent.scratchpad_path,
            deleted_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parent_workflow_id=parent.id,
            parent_step_id="02-compose",
        )
        await repository.create(child)

        result = await handle_list_active_workflows(repository)
        text = result["content"][0]["text"]

        assert parent.workflow_name in text
        assert child.workflow_name not in text


class TestAtomicAutoResume:
    @pytest.mark.asyncio
    async def test_apply_mutation_batch_failure_rolls_back_everything(
        self, repository, tmp_path, mock_registry, monkeypatch
    ):
        """R13 atomic-rollback: if apply_mutation_batch raises mid-flight,
        no DB state should change.
        """
        state = _make_state(
            step_states={"01-plan": "started", "02-execute": "pending", "03-review": "pending"}
        )
        await repository.create(state)

        # Force apply_mutation_batch to raise.
        from tachikoma.workflows.errors import WorkflowRepositoryError  # noqa: PLC0415

        async def boom(_self, _batch):
            raise WorkflowRepositoryError("induced failure")

        monkeypatch.setattr(WorkflowStateRepository, "apply_mutation_batch", boom)

        result = await handle_update_workflow_state(
            state.id,
            "01-plan",
            "complete",
            repository,
            mock_registry,
        )

        assert result.get("is_error") is True

        # State unchanged
        unchanged = await repository.get(state.id)
        assert unchanged.step_states["01-plan"] == "started"
        assert unchanged.step_states["02-execute"] == "pending"
        assert unchanged.current_step is None or unchanged.current_step == "01-plan" or \
               unchanged.current_step != "02-execute"
