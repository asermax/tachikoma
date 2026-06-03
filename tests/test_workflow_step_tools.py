"""Tests for workflow step MCP tools (step_tools.py)."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tachikoma.database import Base
from tachikoma.skills.registry import SkillRegistry
from tachikoma.tasks.repository import TaskRepository
from tachikoma.workflows.definition import StepDefinition, WorkflowDefinition
from tachikoma.workflows.model import STEP_COMPLETED, STEP_PENDING, WorkflowState
from tachikoma.workflows.repository import WorkflowStateRepository
from tachikoma.workflows.step_tools import (
    _MAX_HANDOFF_LENGTH,
    AbortWorkflowArgs,
    CompleteStepArgs,
    RequestInputArgs,
    SkipStepArgs,
    _build_notification_source,
    handle_abort_workflow,
    handle_complete_step,
    handle_request_input,
    handle_skip_step,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session_factory():
    """Create an in-memory SQLite session factory for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def workflow_repository(db_session_factory):
    return WorkflowStateRepository(db_session_factory)


@pytest.fixture
def task_repository(db_session_factory):
    return TaskRepository(db_session_factory)


def _make_step(
    step_id: str,
    title: str,
    required: bool = True,
    condition: str | None = None,
) -> StepDefinition:
    return StepDefinition(
        id=step_id,
        title=title,
        instructions_path=Path(f"/fake/skill/workflows/test/{step_id}/instructions.md"),
        references_path=None,
        scripts_path=None,
        required=required,
        condition=condition,
    )


def _make_state(
    workflow_id: str = "test-wf-id",
    skill_name: str = "test-skill",
    workflow_name: str = "test-workflow",
    step_states: dict | None = None,
    definition_snapshot: list[dict] | None = None,
    current_step: str | None = None,
    scratchpad_path: str | None = None,
) -> WorkflowState:
    if definition_snapshot is None:
        definition_snapshot = [
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
        ]

    if step_states is None:
        step_states = {
            "01-plan": STEP_COMPLETED,
            "02-execute": "started",
            "03-review": STEP_PENDING,
        }

    if scratchpad_path is None:
        scratchpad_path = f"/tmp/test-scratchpad-{workflow_id}.md"

    now = datetime.now(UTC)

    return WorkflowState(
        id=workflow_id,
        skill_name=skill_name,
        workflow_name=workflow_name,
        current_step=current_step or "02-execute",
        step_states=step_states,
        definition_snapshot=definition_snapshot,
        scratchpad_path=scratchpad_path,
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_registry():
    registry = MagicMock(spec=SkillRegistry)
    workflow = WorkflowDefinition(
        skill_name="test-skill",
        workflow_name="test-workflow",
        steps=[
            _make_step("01-plan", "Plan"),
            _make_step("02-execute", "Execute"),
            _make_step("03-review", "Review", required=False),
        ],
        path=Path("/fake/skills/test-skill/workflows/test-workflow"),
    )
    registry.get_workflow.return_value = workflow
    return registry


# ---------------------------------------------------------------------------
# Pydantic model validation
# ---------------------------------------------------------------------------


class TestPydanticModels:
    def test_complete_step_args_defaults(self):
        args = CompleteStepArgs()
        assert args.handoff is None

    def test_complete_step_args_with_handoff(self):
        args = CompleteStepArgs(handoff="summary of work")
        assert args.handoff == "summary of work"

    def test_skip_step_args_empty(self):
        args = SkipStepArgs()
        assert args.model_dump() == {}

    def test_abort_workflow_args_empty(self):
        args = AbortWorkflowArgs()
        assert args.model_dump() == {}

    def test_request_input_args_requires_question(self):
        args = RequestInputArgs(question="What should I do?")
        assert args.question == "What should I do?"

    def test_request_input_args_empty_question_passes_pydantic(self):
        # Pydantic allows empty string; the handler validates
        args = RequestInputArgs(question="")
        assert args.question == ""

    def test_request_input_args_whitespace_passes_pydantic(self):
        args = RequestInputArgs(question="   ")
        assert args.question == "   "


# ---------------------------------------------------------------------------
# Notification source
# ---------------------------------------------------------------------------


class TestNotificationSource:
    def test_builds_source(self):
        source = _build_notification_source("my-skill", "my-workflow")
        assert source == "Workflow: my-skill/my-workflow"


# ---------------------------------------------------------------------------
# handle_complete_step
# ---------------------------------------------------------------------------


class TestHandleCompleteStep:
    async def test_completes_step_and_enqueues_next(
        self, workflow_repository, task_repository, mock_registry
    ):
        state = _make_state()
        await workflow_repository.create(state)

        result = await handle_complete_step(
            "test-wf-id",
            "Work done on step 2",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert not result.get("is_error")
        text = result["content"][0]["text"]
        assert "completed" in text.lower()
        assert "03-review" in text

        # Next step TaskInstance should be created
        chain = await workflow_repository.get_active_chain("test-wf-id")
        assert chain is not None
        assert chain[-1].step_states.get("02-execute") == STEP_COMPLETED

    async def test_stores_handoff_for_next_step(
        self, workflow_repository, task_repository, mock_registry
    ):
        state = _make_state()
        await workflow_repository.create(state)

        await handle_complete_step(
            "test-wf-id",
            "Important context for next step",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        updated = await workflow_repository.get("test-wf-id")
        assert updated is not None
        assert updated.pending_handoff == "Important context for next step"

    async def test_empty_handoff_treated_as_none(
        self, workflow_repository, task_repository, mock_registry
    ):
        state = _make_state()
        await workflow_repository.create(state)

        await handle_complete_step(
            "test-wf-id",
            "   ",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        updated = await workflow_repository.get("test-wf-id")
        assert updated is not None
        assert updated.pending_handoff is None

    async def test_handoff_too_long(self, workflow_repository, task_repository, mock_registry):
        state = _make_state()
        await workflow_repository.create(state)

        result = await handle_complete_step(
            "test-wf-id",
            "x" * (_MAX_HANDOFF_LENGTH + 1),
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert result.get("is_error")
        assert "4000" in result["content"][0]["text"]

    async def test_workflow_not_found(self, workflow_repository, task_repository, mock_registry):
        result = await handle_complete_step(
            "nonexistent-id",
            None,
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert result.get("is_error")
        assert "not found" in result["content"][0]["text"]

    async def test_cascade_validation_error(
        self, workflow_repository, task_repository, mock_registry
    ):
        # Step is pending (not started) — cascade rejects the complete action
        state = _make_state(
            step_states={
                "01-plan": STEP_COMPLETED,
                "02-execute": STEP_PENDING,
                "03-review": STEP_PENDING,
            },
            current_step="02-execute",
        )
        await workflow_repository.create(state)

        result = await handle_complete_step(
            "test-wf-id",
            None,
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert result.get("is_error")
        assert "pending" in result["content"][0]["text"].lower()

    async def test_finalizes_workflow_on_last_step(
        self, workflow_repository, task_repository, mock_registry, tmp_path
    ):
        scratchpad = tmp_path / "scratchpad.md"
        scratchpad.write_text("# test")
        state = _make_state(
            step_states={
                "01-plan": STEP_COMPLETED,
                "02-execute": STEP_COMPLETED,
                "03-review": "started",
            },
            current_step="03-review",
            scratchpad_path=str(scratchpad),
        )
        await workflow_repository.create(state)

        result = await handle_complete_step(
            "test-wf-id",
            None,
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert not result.get("is_error")
        text = result["content"][0]["text"]
        assert "finalized" in text.lower()

        # Scratchpad should be deleted
        assert not scratchpad.exists()


# ---------------------------------------------------------------------------
# handle_skip_step
# ---------------------------------------------------------------------------


class TestHandleSkipStep:
    async def test_skips_optional_step(self, workflow_repository, task_repository, mock_registry):
        # 03-review is optional (required=False)
        state = _make_state(
            step_states={
                "01-plan": STEP_COMPLETED,
                "02-execute": STEP_COMPLETED,
                "03-review": "started",
            },
            current_step="03-review",
        )
        await workflow_repository.create(state)

        result = await handle_skip_step(
            "test-wf-id",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert not result.get("is_error")
        text = result["content"][0]["text"]
        assert "skipped" in text.lower()

    async def test_rejects_required_step(self, workflow_repository, task_repository, mock_registry):
        # 02-execute is required
        state = _make_state(
            step_states={
                "01-plan": STEP_COMPLETED,
                "02-execute": "started",
                "03-review": STEP_PENDING,
            },
            current_step="02-execute",
        )
        await workflow_repository.create(state)

        result = await handle_skip_step(
            "test-wf-id",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert result.get("is_error")
        assert "required" in result["content"][0]["text"]

    async def test_workflow_not_found(self, workflow_repository, task_repository, mock_registry):
        result = await handle_skip_step(
            "nonexistent-id",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        assert result.get("is_error")
        assert "not found" in result["content"][0]["text"]

    async def test_no_active_step(self, workflow_repository, task_repository, mock_registry):
        # No current_step set — handler should error
        state = _make_state(
            step_states={
                "01-plan": STEP_COMPLETED,
                "02-execute": STEP_PENDING,
                "03-review": STEP_PENDING,
            },
            current_step=None,
        )
        await workflow_repository.create(state)

        result = await handle_skip_step(
            "test-wf-id",
            workflow_repository,
            task_repository,
            mock_registry,
        )

        # Either "No step is currently active" or a cascade error is acceptable
        assert result.get("is_error")


# ---------------------------------------------------------------------------
# handle_abort_workflow
# ---------------------------------------------------------------------------


class TestHandleAbortWorkflow:
    async def test_aborts_workflow(self, workflow_repository, tmp_path):
        scratchpad = tmp_path / "scratchpad.md"
        scratchpad.write_text("# test")
        state = _make_state(scratchpad_path=str(scratchpad))
        await workflow_repository.create(state)

        result = await handle_abort_workflow("test-wf-id", workflow_repository)

        assert not result.get("is_error")
        text = result["content"][0]["text"]
        assert "aborted" in text.lower()

        # State should be soft-deleted
        fetched = await workflow_repository.get("test-wf-id")
        assert fetched is None

        # Scratchpad deleted
        assert not scratchpad.exists()

    async def test_workflow_not_found(self, workflow_repository):
        result = await handle_abort_workflow("nonexistent-id", workflow_repository)

        assert result.get("is_error")
        assert "not found" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# handle_request_input
# ---------------------------------------------------------------------------


class TestHandleRequestInput:
    async def test_dispatches_notification(self):
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        result = await handle_request_input(
            "Which option?",
            "Workflow: test-skill/test-workflow",
            "instance-123",
            bus,
        )

        assert not result.get("is_error")
        text = result["content"][0]["text"]
        assert "paused" in text.lower()
        bus.dispatch.assert_called_once()

    async def test_empty_question_rejected(self):
        bus = MagicMock()

        result = await handle_request_input(
            "   ",
            "Workflow: test-skill/test-workflow",
            "instance-123",
            bus,
        )

        assert result.get("is_error")
        assert "cannot be empty" in result["content"][0]["text"]

    async def test_sets_cycle_state(self):
        bus = MagicMock()
        bus.dispatch = AsyncMock()
        cycle_state = MagicMock()
        cycle_state.await_response_requested = False

        await handle_request_input(
            "Question?",
            "Workflow: test-skill/test-workflow",
            "instance-123",
            bus,
            cycle_state=cycle_state,
        )

        assert cycle_state.await_response_requested is True

    async def test_no_cycle_state_no_error(self):
        bus = MagicMock()
        bus.dispatch = AsyncMock()

        result = await handle_request_input(
            "Question?",
            "Workflow: test-skill/test-workflow",
            "instance-123",
            bus,
            cycle_state=None,
        )

        assert not result.get("is_error")
