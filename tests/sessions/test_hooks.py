"""Integration tests for the session recovery bootstrap hook.

Tests for DLT-027: Track conversation sessions.
"""

from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

from tachikoma.bootstrap import BootstrapContext
from tachikoma.config import SettingsManager
from tachikoma.sessions.hooks import session_recovery_hook
from tachikoma.sessions.model import Session
from tachikoma.sessions.registry import SessionRegistry
from tachikoma.sessions.repository import SessionRepository


@pytest.fixture
def settings_manager(tmp_path: Path) -> SettingsManager:
    config_path = tmp_path / "config.toml"
    workspace_path = tmp_path / "workspace"
    config_path.write_text(f'[workspace]\npath = "{workspace_path}"\n')
    return SettingsManager(config_path)


@pytest.fixture
async def ctx(settings_manager: SettingsManager) -> BootstrapContext:
    # Ensure workspace and data dirs exist (normally created by workspace_hook)
    ws = settings_manager.settings.workspace
    ws.path.mkdir(parents=True, exist_ok=True)
    ws.data_path.mkdir(exist_ok=True)

    ctx = BootstrapContext(settings_manager=settings_manager, prompt=input)
    yield ctx

    # Close the repository if the hook created one, to release SQLite connections
    repo = ctx.extras.get("session_repository")
    if repo is not None:
        await repo.close()


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

    async def test_creates_database_file(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: hook creates the sessions.db file in the data directory."""
        db_path = settings_manager.settings.workspace.data_path / "sessions.db"
        assert not db_path.exists()

        await session_recovery_hook(ctx)

        assert db_path.exists()

    async def test_recovers_interrupted_sessions(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: hook closes sessions left open from a previous run."""
        data_path = settings_manager.settings.workspace.data_path

        # Pre-populate a database with an open session
        repo = SessionRepository(data_path / "sessions.db")
        await repo.initialize()
        open_session = Session(id="open-abc", started_at=datetime.now(UTC))
        await repo.create(open_session)
        await repo.close()

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

    async def test_runs_migration_before_recovery(
        self, ctx: BootstrapContext, settings_manager: SettingsManager
    ) -> None:
        """AC: migration runs before recovery, adding summary column to existing DB."""

        data_path = settings_manager.settings.workspace.data_path
        db_path = data_path / "sessions.db"

        # Pre-populate a database with the OLD schema (no summary column)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    sdk_session_id TEXT,
                    transcript_path TEXT,
                    started_at TEXT,
                    ended_at TEXT
                )"""
            )
            # Add an open session that needs recovery
            await db.execute(
                "INSERT INTO sessions (id, started_at) VALUES ('open-xyz', ?)",
                (datetime.now(UTC).isoformat(),),
            )
            await db.commit()

        # Verify summary column doesn't exist
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM pragma_table_info('sessions') WHERE name='summary'"
            )
            row = await cursor.fetchone()
            assert row is None

        # Run the hook — should migrate AND recover
        await session_recovery_hook(ctx)

        # Verify the column was added
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM pragma_table_info('sessions') WHERE name='summary'"
            )
            row = await cursor.fetchone()
            assert row is not None

        # Verify recovery also happened
        repo2: SessionRepository = ctx.extras["session_repository"]
        recovered = await repo2.get_by_id("open-xyz")
        assert recovered is not None
        assert recovered.ended_at is not None
