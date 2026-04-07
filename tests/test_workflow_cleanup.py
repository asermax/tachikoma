"""Tests for stale workflow cleanup processor."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tachikoma.database import Base
from tachikoma.sessions.model import Session
from tachikoma.workflows.cleanup import StaleWorkflowCleanupProcessor
from tachikoma.workflows.model import WorkflowState
from tachikoma.workflows.repository import WorkflowStateRepository


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def repository(session_factory):
    return WorkflowStateRepository(session_factory)


def _make_state(
    workflow_id: str = "stale-wf",
    workflow_name: str = "test-workflow",
    updated_at: datetime | None = None,
) -> WorkflowState:
    now = datetime.now(UTC)
    return WorkflowState(
        id=workflow_id,
        skill_name="test-skill",
        workflow_name=workflow_name,
        current_step=None,
        step_states={"01-plan": "pending"},
        definition_snapshot=[{"id": "01-plan", "title": "Plan", "skippable": False}],
        scratchpad_path=f"/tmp/scratchpad-{workflow_id}.md",
        deleted_at=None,
        created_at=now,
        updated_at=updated_at or now,
    )


def _make_session() -> Session:
    """Create a minimal Session for the processor."""
    return Session(
        id="test-session",
        sdk_session_id="sdk-test-session",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        summary=None,
        last_resumed_at=None,
        processed_at=None,
        error=False,
    )


class TestStaleWorkflowCleanup:
    @pytest.mark.asyncio
    async def test_stale_workflow_cleaned(self, repository, tmp_path):
        old_time = datetime.now(UTC) - timedelta(hours=25)
        state = _make_state("stale-wf", updated_at=old_time)
        await repository.create(state)

        processor = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor.process(_make_session())

        # Verify soft-deleted
        assert await repository.get("stale-wf") is None

    @pytest.mark.asyncio
    async def test_fresh_workflow_preserved(self, repository, tmp_path):
        state = _make_state("fresh-wf", updated_at=datetime.now(UTC))
        await repository.create(state)

        processor = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor.process(_make_session())

        # Verify still active
        assert await repository.get("fresh-wf") is not None

    @pytest.mark.asyncio
    async def test_scratchpad_deleted_on_cleanup(self, repository, tmp_path):
        scratchpad = tmp_path / "scratchpad-stale.md"
        scratchpad.write_text("workflow notes")

        old_time = datetime.now(UTC) - timedelta(hours=25)
        state = WorkflowState(
            id="stale-with-scratchpad",
            skill_name="test-skill",
            workflow_name="test-workflow",
            current_step=None,
            step_states={"01-plan": "pending"},
            definition_snapshot=[{"id": "01-plan", "title": "Plan", "skippable": False}],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=old_time,
            updated_at=old_time,
        )
        await repository.create(state)

        processor = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor.process(_make_session())

        assert not scratchpad.exists()

    @pytest.mark.asyncio
    async def test_custom_threshold(self, repository, tmp_path):
        # Workflow updated 2 hours ago
        two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
        state = _make_state("two-hour-wf", updated_at=two_hours_ago)
        await repository.create(state)

        # Default threshold (24h) should preserve it
        processor_24h = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor_24h.process(_make_session())
        assert await repository.get("two-hour-wf") is not None

        # 1h threshold should clean it
        processor_1h = StaleWorkflowCleanupProcessor(
            repository,
            tmp_path,
            threshold=timedelta(hours=1),
        )
        await processor_1h.process(_make_session())
        assert await repository.get("two-hour-wf") is None

    @pytest.mark.asyncio
    async def test_no_stale_workflows(self, repository, tmp_path):
        # No workflows at all
        processor = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor.process(_make_session())  # Should not raise

    @pytest.mark.asyncio
    async def test_mixed_stale_and_fresh(self, repository, tmp_path):
        old_time = datetime.now(UTC) - timedelta(hours=25)
        fresh_time = datetime.now(UTC)

        stale_state = _make_state("stale-1", updated_at=old_time)
        fresh_state = _make_state(
            "fresh-1",
            workflow_name="fresh-workflow",
            updated_at=fresh_time,
        )
        await repository.create(stale_state)
        await repository.create(fresh_state)

        processor = StaleWorkflowCleanupProcessor(repository, tmp_path)
        await processor.process(_make_session())

        assert await repository.get("stale-1") is None
        assert await repository.get("fresh-1") is not None
