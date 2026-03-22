"""Integration tests for the session recovery bootstrap hook.

Tests for DLT-027: Track conversation sessions.
"""

from datetime import UTC, datetime

import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.database import Database
from tachikoma.sessions.hooks import session_recovery_hook
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace and data dirs exist (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.data_path.mkdir(exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)

    # Initialize the shared database (normally done by database_hook)
    database = Database(ws.data_path / "tachikoma.db")
    await database.initialize()
    ctx.extras["database"] = database

    yield ctx

    await database.close()


class TestSessionRecoveryHook:
    """Tests for session_recovery_hook."""

    async def test_stores_repository_in_extras(self, ctx: BootstrapContext) -> None:
        """AC: hook stores repository in ctx.extras['session_repository']."""
        await session_recovery_hook(ctx)

        assert "session_repository" in ctx.extras
        assert isinstance(ctx.extras["session_repository"], SessionRepository)

    async def test_stores_registry_in_extras(self, ctx: BootstrapContext) -> None:
        """AC: hook stores registry in ctx.extras['session_registry']."""
        await session_recovery_hook(ctx)

        assert "session_registry" in ctx.extras
        assert isinstance(ctx.extras["session_registry"], SessionRegistry)

    async def test_recovers_interrupted_sessions(
        self, ctx: BootstrapContext
    ) -> None:
        """AC: hook closes sessions left open from a previous run."""
        database: Database = ctx.extras["database"]

        # Pre-populate with an open session
        repo = SessionRepository(database.session_factory)
        open_session = Session(id="open-abc", started_at=datetime.now(UTC))
        await repo.create(open_session)

        # Run the hook — should recover the open session
        await session_recovery_hook(ctx)

        # Verify the session was closed
        repo2: SessionRepository = ctx.extras["session_repository"]
        recovered = await repo2.get_by_id("open-abc")

        assert recovered is not None
        assert recovered.ended_at is not None
        assert recovered.status in ("interrupted", "closed")

    async def test_idempotent_when_no_open_sessions(self, ctx: BootstrapContext) -> None:
        """AC: hook with no open sessions completes without error."""
        await session_recovery_hook(ctx)

        registry: SessionRegistry = ctx.extras["session_registry"]
        active = await registry.get_active_session()
        assert active is None
