"""Tests for WorkflowStateRepository.

Uses in-memory SQLite with real async SQLAlchemy sessions for integration-style testing.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tachikoma.database import Base
from tachikoma.workflows.model import (
    StepState,
    WorkflowState,
    WorkflowStateRecord,
)
from tachikoma.workflows.repository import WorkflowStateRepository


@pytest.fixture
async def session_factory():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def sample_workflow_state():
    """Create a sample workflow state for testing."""
    now = datetime.now(UTC)
    return WorkflowState(
        id="test-workflow-id",
        skill_name="test_skill",
        workflow_name="test_workflow",
        current_step="step1",
        step_states={
            "step1": "started",  # type: ignore[dict-item]
            "step2": "pending",  # type: ignore[dict-item]
            "step3": "pending",  # type: ignore[dict-item]
        },
        definition_snapshot=[
            {"name": "step1", "description": "First step"},
            {"name": "step2", "description": "Second step"},
            {"name": "step3", "description": "Third step"},
        ],
        scratchpad_path="/tmp/test/scratchpad.json",
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Tests for dataclass and serialization
# ---------------------------------------------------------------------------


def test_workflow_state_dataclass_construction(sample_workflow_state):
    """Test that WorkflowState dataclass can be constructed."""
    assert sample_workflow_state.id == "test-workflow-id"
    assert sample_workflow_state.skill_name == "test_skill"
    assert sample_workflow_state.workflow_name == "test_workflow"
    assert sample_workflow_state.current_step == "step1"
    assert len(sample_workflow_state.step_states) == 3
    assert len(sample_workflow_state.definition_snapshot) == 3
    assert sample_workflow_state.scratchpad_path == "/tmp/test/scratchpad.json"
    assert sample_workflow_state.deleted_at is None


def test_step_states_serialization_round_trip():
    """Test that step_states serialize and deserialize correctly."""
    original: dict[str, StepState] = {
        "step1": "pending",
        "step2": "started",
        "step3": "completed",
        "step4": "skipped",
    }

    serialized = json.dumps(original)
    deserialized = dict(json.loads(serialized))

    assert deserialized == original


def test_definition_snapshot_serialization_round_trip():
    """Test that definition_snapshot serialize and deserialize correctly."""
    original = [
        {"name": "step1", "description": "First step", "tool": "tool1"},
        {"name": "step2", "description": "Second step", "tool": "tool2"},
        {"name": "step3", "description": "Third step", "tool": "tool3"},
    ]

    serialized = json.dumps(original)
    deserialized = json.loads(serialized)

    assert deserialized == original


# ---------------------------------------------------------------------------
# Tests for ORM model
# ---------------------------------------------------------------------------


def test_workflow_state_record_to_domain(sample_workflow_state):
    """Test that WorkflowStateRecord.to_domain() converts correctly."""
    record = WorkflowStateRecord(
        id=sample_workflow_state.id,
        skill_name=sample_workflow_state.skill_name,
        workflow_name=sample_workflow_state.workflow_name,
        current_step=sample_workflow_state.current_step,
        step_states=json.dumps(sample_workflow_state.step_states),
        definition_snapshot=json.dumps(
            sample_workflow_state.definition_snapshot
        ),
        scratchpad_path=sample_workflow_state.scratchpad_path,
        deleted_at=sample_workflow_state.deleted_at,
        created_at=sample_workflow_state.created_at,
        updated_at=sample_workflow_state.updated_at,
    )

    domain = record.to_domain()

    assert domain.id == sample_workflow_state.id
    assert domain.skill_name == sample_workflow_state.skill_name
    assert domain.workflow_name == sample_workflow_state.workflow_name
    assert domain.current_step == sample_workflow_state.current_step
    assert domain.step_states == sample_workflow_state.step_states
    assert domain.definition_snapshot == sample_workflow_state.definition_snapshot
    assert domain.scratchpad_path == sample_workflow_state.scratchpad_path
    assert domain.deleted_at is None


# ---------------------------------------------------------------------------
# Tests for repository CRUD operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_workflow_state(session_factory, sample_workflow_state):
    """Test creating a workflow state."""
    repo = WorkflowStateRepository(session_factory)
    created = await repo.create(sample_workflow_state)

    assert created.id == sample_workflow_state.id
    assert created.skill_name == sample_workflow_state.skill_name
    assert created.workflow_name == sample_workflow_state.workflow_name
    assert created.current_step == sample_workflow_state.current_step
    assert created.step_states == sample_workflow_state.step_states
    assert created.definition_snapshot == sample_workflow_state.definition_snapshot


@pytest.mark.asyncio
async def test_get_workflow_state(session_factory, sample_workflow_state):
    """Test retrieving a workflow state by ID."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    retrieved = await repo.get(sample_workflow_state.id)

    assert retrieved is not None
    assert retrieved.id == sample_workflow_state.id
    assert retrieved.skill_name == sample_workflow_state.skill_name
    assert retrieved.workflow_name == sample_workflow_state.workflow_name


@pytest.mark.asyncio
async def test_get_nonexistent_workflow_state(session_factory):
    """Test retrieving a non-existent workflow state returns None."""
    repo = WorkflowStateRepository(session_factory)
    retrieved = await repo.get("nonexistent-id")
    assert retrieved is None


@pytest.mark.asyncio
async def test_get_active_workflow_state(session_factory, sample_workflow_state):
    """Test retrieving the active workflow state for a skill/workflow combination."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    retrieved = await repo.get_active(
        sample_workflow_state.skill_name, sample_workflow_state.workflow_name
    )

    assert retrieved is not None
    assert retrieved.id == sample_workflow_state.id


@pytest.mark.asyncio
async def test_get_active_when_none_exists(session_factory):
    """Test get_active returns None when no active state exists."""
    repo = WorkflowStateRepository(session_factory)
    retrieved = await repo.get_active("nonexistent_skill", "nonexistent_workflow")
    assert retrieved is None


@pytest.mark.asyncio
async def test_update_workflow_state(session_factory, sample_workflow_state):
    """Test updating a workflow state."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    updated = await repo.update(
        sample_workflow_state.id,
        current_step="step2",
        step_states={
            "step1": "completed",  # type: ignore[dict-item]
            "step2": "started",  # type: ignore[dict-item]
            "step3": "pending",  # type: ignore[dict-item]
        },
    )

    assert updated is not None
    assert updated.current_step == "step2"
    assert updated.step_states["step1"] == "completed"
    assert updated.step_states["step2"] == "started"
    assert updated.step_states["step3"] == "pending"


@pytest.mark.asyncio
async def test_update_bumps_updated_at(session_factory, sample_workflow_state):
    """Test that update always bumps the updated_at timestamp."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    # Wait a bit to ensure timestamp difference
    await asyncio.sleep(0.01)

    updated = await repo.update(sample_workflow_state.id, current_step="step2")

    assert updated is not None
    assert updated.updated_at > sample_workflow_state.updated_at


@pytest.mark.asyncio
async def test_update_nonexistent_workflow_state(session_factory):
    """Test updating a non-existent workflow state returns None."""
    repo = WorkflowStateRepository(session_factory)
    updated = await repo.update("nonexistent-id", current_step="step2")
    assert updated is None


@pytest.mark.asyncio
async def test_soft_delete_workflow_state(session_factory, sample_workflow_state):
    """Test soft deleting a workflow state."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    deleted = await repo.soft_delete(sample_workflow_state.id)

    assert deleted is True

    # Should not be retrievable via get()
    retrieved = await repo.get(sample_workflow_state.id)
    assert retrieved is None

    # Should not appear in list_active()
    active = await repo.list_active()
    assert len(active) == 0


@pytest.mark.asyncio
async def test_soft_delete_nonexistent_workflow_state(session_factory):
    """Test soft deleting a non-existent workflow state returns False."""
    repo = WorkflowStateRepository(session_factory)
    deleted = await repo.soft_delete("nonexistent-id")
    assert deleted is False


@pytest.mark.asyncio
async def test_list_active(session_factory):
    """Test listing all active workflow states."""
    repo = WorkflowStateRepository(session_factory)

    now = datetime.now(UTC)

    state1 = WorkflowState(
        id="workflow-1",
        skill_name="skill1",
        workflow_name="workflow1",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch1.json",
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )

    state2 = WorkflowState(
        id="workflow-2",
        skill_name="skill2",
        workflow_name="workflow2",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )

    await repo.create(state1)
    await repo.create(state2)

    active = await repo.list_active()

    assert len(active) == 2
    assert any(s.id == "workflow-1" for s in active)
    assert any(s.id == "workflow-2" for s in active)


@pytest.mark.asyncio
async def test_list_active_filters_out_deleted(session_factory, sample_workflow_state):
    """Test that list_active filters out soft-deleted states."""
    repo = WorkflowStateRepository(session_factory)

    now = datetime.now(UTC)

    active_state = WorkflowState(
        id="active-workflow",
        skill_name="skill1",
        workflow_name="workflow1",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch1.json",
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )

    deleted_state = WorkflowState(
        id="deleted-workflow",
        skill_name="skill2",
        workflow_name="workflow2",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=datetime.now(UTC),
        created_at=now,
        updated_at=now,
    )

    await repo.create(active_state)
    await repo.create(deleted_state)

    active = await repo.list_active()

    assert len(active) == 1
    assert active[0].id == "active-workflow"


@pytest.mark.asyncio
async def test_list_stale(session_factory):
    """Test listing stale workflow states."""
    repo = WorkflowStateRepository(session_factory)

    now = datetime.now(UTC)

    stale_state = WorkflowState(
        id="stale-workflow",
        skill_name="skill1",
        workflow_name="workflow1",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch1.json",
        deleted_at=None,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )

    fresh_state = WorkflowState(
        id="fresh-workflow",
        skill_name="skill2",
        workflow_name="workflow2",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=None,
        created_at=now,
        updated_at=now,
    )

    await repo.create(stale_state)
    await repo.create(fresh_state)

    stale = await repo.list_stale(threshold=timedelta(hours=1))

    assert len(stale) == 1
    assert stale[0].id == "stale-workflow"


@pytest.mark.asyncio
async def test_list_stale_filters_out_deleted(session_factory):
    """Test that list_stale filters out soft-deleted states."""
    repo = WorkflowStateRepository(session_factory)

    now = datetime.now(UTC)

    stale_active = WorkflowState(
        id="stale-active",
        skill_name="skill1",
        workflow_name="workflow1",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch1.json",
        deleted_at=None,
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )

    stale_deleted = WorkflowState(
        id="stale-deleted",
        skill_name="skill2",
        workflow_name="workflow2",
        current_step="step1",
        step_states={"step1": "started"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=2),
        updated_at=now - timedelta(hours=2),
    )

    await repo.create(stale_active)
    await repo.create(stale_deleted)

    stale = await repo.list_stale(threshold=timedelta(hours=1))

    assert len(stale) == 1
    assert stale[0].id == "stale-active"


@pytest.mark.asyncio
async def test_duplicate_prevention(session_factory, sample_workflow_state):
    """Test that creating a duplicate active state raises an error."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    # Try to create another state with the same skill_name + workflow_name
    duplicate = WorkflowState(
        id="different-id",
        skill_name=sample_workflow_state.skill_name,
        workflow_name=sample_workflow_state.workflow_name,
        current_step="step1",
        step_states={"step1": "pending"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    with pytest.raises(Exception) as exc_info:
        await repo.create(duplicate)

    assert "Active workflow state already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_after_soft_delete(session_factory, sample_workflow_state):
    """Test that creating a state after soft delete works correctly."""
    repo = WorkflowStateRepository(session_factory)
    await repo.create(sample_workflow_state)

    # Soft delete the first state
    await repo.soft_delete(sample_workflow_state.id)

    # Should be able to create a new state with the same skill_name + workflow_name
    new_state = WorkflowState(
        id="new-state-id",
        skill_name=sample_workflow_state.skill_name,
        workflow_name=sample_workflow_state.workflow_name,
        current_step="step1",
        step_states={"step1": "pending"},  # type: ignore[dict-item]
        definition_snapshot=[{"name": "step1"}],
        scratchpad_path="/tmp/scratch2.json",
        deleted_at=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    created = await repo.create(new_state)
    assert created.id == "new-state-id"
