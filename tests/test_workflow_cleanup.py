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
        definition_snapshot=[{"id": "01-plan", "title": "Plan", "required": True}],
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

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        # Verify soft-deleted
        assert await repository.get("stale-wf") is None

    @pytest.mark.asyncio
    async def test_fresh_workflow_preserved(self, repository, tmp_path):
        state = _make_state("fresh-wf", updated_at=datetime.now(UTC))
        await repository.create(state)

        processor = StaleWorkflowCleanupProcessor(repository)
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
            definition_snapshot=[{"id": "01-plan", "title": "Plan", "required": True}],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=old_time,
            updated_at=old_time,
        )
        await repository.create(state)

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        assert not scratchpad.exists()

    @pytest.mark.asyncio
    async def test_custom_threshold(self, repository, tmp_path):
        # Workflow updated 2 hours ago
        two_hours_ago = datetime.now(UTC) - timedelta(hours=2)
        state = _make_state("two-hour-wf", updated_at=two_hours_ago)
        await repository.create(state)

        # Default threshold (24h) should preserve it
        processor_24h = StaleWorkflowCleanupProcessor(repository)
        await processor_24h.process(_make_session())
        assert await repository.get("two-hour-wf") is not None

        # 1h threshold should clean it
        processor_1h = StaleWorkflowCleanupProcessor(
            repository,
            threshold=timedelta(hours=1),
        )
        await processor_1h.process(_make_session())
        assert await repository.get("two-hour-wf") is None

    @pytest.mark.asyncio
    async def test_no_stale_workflows(self, repository):
        # No workflows at all
        processor = StaleWorkflowCleanupProcessor(repository)
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

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        assert await repository.get("stale-1") is None
        assert await repository.get("fresh-1") is not None


class TestSubtreeCleanup:
    """R16: subtree-aware staleness — fresh child keeps parent alive,
    aborts cascade when entire subtree is stale.
    """

    def _make_child_state(
        self,
        workflow_id: str,
        parent_id: str,
        parent_step_id: str,
        scratchpad_path: str,
        updated_at: datetime,
    ) -> WorkflowState:
        return WorkflowState(
            id=workflow_id,
            skill_name="test-skill",
            workflow_name=f"child-{workflow_id}",
            current_step=None,
            step_states={"01-only": "started"},
            definition_snapshot=[{"id": "01-only", "title": "Only", "required": True}],
            scratchpad_path=scratchpad_path,
            deleted_at=None,
            created_at=updated_at,
            updated_at=updated_at,
            parent_workflow_id=parent_id,
            parent_step_id=parent_step_id,
        )

    @pytest.mark.asyncio
    async def test_fresh_child_keeps_old_parent_alive(self, repository, tmp_path):
        """R16 first AC: stale parent + fresh child → neither deleted."""
        scratchpad = tmp_path / "subtree-fresh.md"
        scratchpad.write_text("# subtree")

        old = datetime.now(UTC) - timedelta(hours=30)
        fresh = datetime.now(UTC) - timedelta(minutes=5)

        parent = WorkflowState(
            id="parent-fresh-child",
            skill_name="test-skill",
            workflow_name="parent-wf",
            current_step="02-compose",
            step_states={"02-compose": "started"},
            definition_snapshot=[
                {
                    "id": "02-compose",
                    "title": "Compose",
                    "required": True,
                    "composes": "child-wf",
                }
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=old,
            updated_at=old,
        )
        await repository.create(parent)

        child = self._make_child_state(
            "child-fresh",
            parent.id,
            "02-compose",
            str(scratchpad),
            fresh,
        )
        await repository.create(child)

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        # Both still active, scratchpad still on disk
        assert await repository.get(parent.id) is not None
        assert await repository.get(child.id) is not None
        assert scratchpad.exists()

    @pytest.mark.asyncio
    async def test_old_subtree_cascades(self, repository, tmp_path):
        """R16 second AC: stale parent + stale child → both deleted, single
        scratchpad delete.
        """
        scratchpad = tmp_path / "subtree-old.md"
        scratchpad.write_text("# subtree")

        old = datetime.now(UTC) - timedelta(hours=30)

        parent = WorkflowState(
            id="parent-old-child",
            skill_name="test-skill",
            workflow_name="parent-wf",
            current_step="02-compose",
            step_states={"02-compose": "started"},
            definition_snapshot=[
                {
                    "id": "02-compose",
                    "title": "Compose",
                    "required": True,
                    "composes": "child-wf",
                }
            ],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=old,
            updated_at=old,
        )
        await repository.create(parent)

        child = self._make_child_state(
            "child-old",
            parent.id,
            "02-compose",
            str(scratchpad),
            old,
        )
        await repository.create(child)

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        # Both gone, scratchpad deleted exactly once
        assert await repository.get(parent.id) is None
        assert await repository.get(child.id) is None
        assert not scratchpad.exists()

    @pytest.mark.asyncio
    async def test_three_level_chain_all_old_cascade(self, repository, tmp_path):
        """R16 + R17: a 3-level chain that is entirely stale is fully cascaded
        in one cleanup pass.
        """
        scratchpad = tmp_path / "subtree-3.md"
        scratchpad.write_text("# subtree-3")

        old = datetime.now(UTC) - timedelta(hours=30)

        parent = WorkflowState(
            id="three-parent",
            skill_name="test-skill",
            workflow_name="three-parent-wf",
            current_step="01",
            step_states={"01": "started"},
            definition_snapshot=[{"id": "01", "title": "01", "required": True, "composes": "mid"}],
            scratchpad_path=str(scratchpad),
            deleted_at=None,
            created_at=old,
            updated_at=old,
        )
        await repository.create(parent)

        mid = self._make_child_state(
            "three-mid",
            parent.id,
            "01",
            str(scratchpad),
            old,
        )
        await repository.create(mid)

        leaf = self._make_child_state(
            "three-leaf",
            "three-mid",
            "01-only",
            str(scratchpad),
            old,
        )
        await repository.create(leaf)

        processor = StaleWorkflowCleanupProcessor(repository)
        await processor.process(_make_session())

        for wid in ("three-parent", "three-mid", "three-leaf"):
            assert await repository.get(wid) is None
        assert not scratchpad.exists()
