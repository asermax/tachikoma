"""Tests for workflow MCP tools, handlers, and transition validation."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tachikoma.database import Base
from tachikoma.skills.registry import Skill, SkillRegistry
from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition
from tachikoma.workflows.model import WorkflowState
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.tools import (
    EndWorkflowArgs,
    GetWorkflowStateArgs,
    ListActiveWorkflowsArgs,
    StartWorkflowArgs,
    UpdateWorkflowStateArgs,
    _find_next_pending_step,
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


def _make_step(step_id: str, title: str, skippable: bool = False) -> StepDefinition:
    """Create a test StepDefinition."""
    return StepDefinition(
        id=step_id,
        title=title,
        instructions_path=Path(f"/fake/skill/workflows/test/{step_id}/instructions.md"),
        references_path=None,
        scripts_path=None,
        skippable=skippable,
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
            {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
            {
                "id": "02-execute",
                "title": "Execute",
                "skippable": False,
                "path": "/fake/02-execute",
            },
            {"id": "03-review", "title": "Review", "skippable": True, "path": "/fake/03-review"},
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
        {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
        {"id": "02-execute", "title": "Execute", "skippable": False, "path": "/fake/02-execute"},
        {"id": "03-review", "title": "Review", "skippable": True, "path": "/fake/03-review"},
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

    def test_skip_skippable_pending_step_valid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        assert validate_transition(states, "03-review", "skip", self.SNAPSHOT) is None

    def test_skip_non_skippable_step_invalid(self):
        states = {"01-plan": "pending", "02-execute": "pending", "03-review": "pending"}
        error = validate_transition(states, "01-plan", "skip", self.SNAPSHOT)
        assert error is not None
        assert "not skippable" in error

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
        step = _make_step("01-plan", "Plan", skippable=True)
        snapshot = _step_to_snapshot(step)

        assert snapshot["id"] == "01-plan"
        assert snapshot["title"] == "Plan"
        assert snapshot["skippable"] is True
        assert snapshot["path"] == str(step.instructions_path.parent)
        assert snapshot["required_skills"] == []

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
                {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "skippable": False,
                    "path": "/fake/02-execute",
                },
                {
                    "id": "03-review",
                    "title": "Review",
                    "skippable": True,
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
    async def test_skip_skippable_step(self, repository, tmp_path, mock_registry):
        state = _make_state(
            step_states={
                "01-plan": "completed",
                "02-execute": "pending",
                "03-review": "pending",
            },
            definition_snapshot=[
                {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "skippable": True,
                    "path": "/fake/02-execute",
                },
                {
                    "id": "03-review",
                    "title": "Review",
                    "skippable": False,
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
    async def test_skip_non_skippable_rejected(self, repository, tmp_path, mock_registry):
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
        assert "not skippable" in result["content"][0]["text"]

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
                {"id": "01-plan", "title": "Plan", "skippable": False, "path": "/fake/01-plan"},
                {
                    "id": "02-execute",
                    "title": "Execute",
                    "skippable": False,
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
                "skippable": False,
                "path": "/fake/01-plan",
                "required_skills": ["skill-a"],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "skippable": False,
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
                "skippable": False,
                "path": "/fake/01-plan",
                "required_skills": [],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "skippable": False,
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
    async def test_skip_auto_start_injects_next_step_required_skills(
        self, repository, tmp_path
    ):
        snapshot = [
            {
                "id": "01-plan",
                "title": "Plan",
                "skippable": True,
                "path": "/fake/01-plan",
                "required_skills": [],
            },
            {
                "id": "02-execute",
                "title": "Execute",
                "skippable": False,
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
                "skippable": False,
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
